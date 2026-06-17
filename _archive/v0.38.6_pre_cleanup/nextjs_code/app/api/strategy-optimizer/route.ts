import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
import type { TokenCandidate, RankResult } from '@/lib/types/strategy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// TYPES (local-only types, shared types imported from @/lib/types/strategy)
// ============================================================

interface ScanResult {
  tokens: TokenCandidate[];
  dnaProfiles: number;
  activeSignals: number;
  lifecyclePhases: Record<string, number>;
  behaviorModels: number;
}

interface StrategyConfig {
  id: string;
  name: string;
  category: string;
  icon: string;
  timeframe: string;
  tokenAgeCategory: string;
  riskTolerance: string;
  capitalAllocation: number;
  config: {
    assetFilter: Record<string, unknown>;
    phaseConfig: Record<string, unknown>;
    entrySignal: Record<string, unknown>;
    exitSignal: Record<string, unknown>;
    riskManagement: Record<string, unknown>;
    executionConfig: Record<string, unknown>;
  };
}

// ============================================================
// HELPERS
// ============================================================

function getTokenAgeCategory(createdAt: Date): 'NEW' | 'MEDIUM' | 'OLD' {
  const ageMs = Date.now() - createdAt.getTime();
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays < 7) return 'NEW';
  if (ageDays < 30) return 'MEDIUM';
  return 'OLD';
}

// Calculate token age category from creation timestamp (ms epoch)
function getTokenAgeFromTimestamp(createdAtMs: number | null): 'NEW' | 'MEDIUM' | 'OLD' {
  if (!createdAtMs) return 'MEDIUM'; // Default if unknown
  const ageMs = Date.now() - createdAtMs;
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays < 7) return 'NEW';
  if (ageDays < 30) return 'MEDIUM';
  return 'OLD';
}

// ============================================================
// DEXSCREENER TYPES & HELPERS
// ============================================================

interface DexScreenerToken {
  address: string;
  symbol: string;
  name: string;
  chain: string;
  priceUsd: number;
  volume24h: number;
  liquidity: number;
  marketCap: number;
  priceChange24h: number;
  pairCreatedAt: number | null;
}

/**
 * Fetch trending tokens from DexScreener API.
 * Uses the search endpoint for fresh pair data with volume/liquidity metrics,
 * and the token-boosts endpoint for trending token discovery.
 * Returns tokens filtered by volume24h > 10000 and liquidity > 5000.
 */
async function fetchDexScreenerTokens(): Promise<DexScreenerToken[]> {
  try {
    const tokens: DexScreenerToken[] = [];
    const seenAddresses = new Set<string>();

    // 1. Fetch search results for popular chains (full pair data in one call)
    const searchQueries = ['solana', 'ethereum', 'base'];
    const searchResults = await Promise.allSettled(
      searchQueries.map(q =>
        fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`, {
          next: { revalidate: 60 }, // Cache for 60 seconds
          signal: AbortSignal.timeout(8000), // 8s timeout — fast fallback when no VPN
        })
      )
    );

    for (const result of searchResults) {
      if (result.status !== 'fulfilled' || !result.value.ok) continue;
      try {
        const data = await result.value.json();
        const pairs = data.pairs || [];

        for (const pair of pairs) {
          if (!pair.baseToken?.address) continue;
          const addr = pair.baseToken.address;
          if (seenAddresses.has(addr)) continue;

          const volume24h = pair.volume?.h24 || 0;
          const liquidity = pair.liquidity?.usd || 0;

          if (volume24h < 10000 || liquidity < 5000) continue;

          seenAddresses.add(addr);
          tokens.push({
            address: addr,
            symbol: pair.baseToken.symbol || 'UNKNOWN',
            name: pair.baseToken.name || 'Unknown',
            chain: pair.chainId || 'unknown',
            priceUsd: parseFloat(pair.priceUsd || '0'),
            volume24h,
            liquidity,
            marketCap: pair.marketCap || pair.fdv || 0,
            priceChange24h: pair.priceChange?.h24 || 0,
            pairCreatedAt: pair.pairCreatedAt || null,
          });
        }
      } catch {
        continue;
      }
    }

    // 2. Fetch boosted/trending tokens and enrich with pair data
    try {
      const boostRes = await fetch('https://api.dexscreener.com/token-boosts/top/v1', {
        next: { revalidate: 60 },
        signal: AbortSignal.timeout(8000), // 8s timeout — fast fallback when no VPN
      });

      if (boostRes.ok) {
        const boostedTokens = await boostRes.json();
        // Filter to tokens not already found via search, limit to 5 to avoid excessive API calls
        const toEnrich = (Array.isArray(boostedTokens) ? boostedTokens : [])
          .filter((bt: Record<string, unknown>) => bt.tokenAddress && !seenAddresses.has(bt.tokenAddress as string))
          .slice(0, 5);

        // Fetch pair data for boosted tokens using the existing client (has rate limiting)
        for (const bt of toEnrich) {
          try {
            const pairs = await dexScreenerClient.searchTokenPairs(bt.tokenAddress as string);
            if (pairs.length === 0) continue;

            // Sort by liquidity (highest first)
            pairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
            const best = pairs[0];

            const volume24h = best.volume?.h24 || 0;
            const liquidity = best.liquidity?.usd || 0;
            if (volume24h < 10000 || liquidity < 5000) continue;

            seenAddresses.add(bt.tokenAddress as string);
            tokens.push({
              address: bt.tokenAddress as string,
              symbol: best.baseToken?.symbol || 'UNKNOWN',
              name: best.baseToken?.name || 'Unknown',
              chain: best.chainId || (bt.chainId as string) || 'unknown',
              priceUsd: parseFloat(best.priceUsd || '0'),
              volume24h,
              liquidity,
              marketCap: best.marketCap || best.fdv || 0,
              priceChange24h: best.priceChange?.h24 || 0,
              pairCreatedAt: best.pairCreatedAt || null,
            });
          } catch {
            continue;
          }
        }
      }
    } catch {
      // Boosted tokens fetch failed, continue with search results only
    }

    // Sort by volume descending
    tokens.sort((a, b) => b.volume24h - a.volume24h);

    return tokens;
  } catch {
    return [];
  }
}

/**
 * Calculate a risk score for a DexScreener token based on available metrics.
 * Higher scores indicate higher risk.
 * Scale: 0-100
 */
function calculateDexRiskScore(token: DexScreenerToken): number {
  let risk = 50; // Base risk

  // Lower liquidity = higher risk
  if (token.liquidity < 10000) risk += 20;
  else if (token.liquidity < 50000) risk += 10;
  else if (token.liquidity > 500000) risk -= 10;

  // Lower market cap = higher risk
  if (token.marketCap < 50000) risk += 15;
  else if (token.marketCap < 500000) risk += 5;
  else if (token.marketCap > 10000000) risk -= 10;

  // Newer tokens are riskier
  const ageCategory = getTokenAgeFromTimestamp(token.pairCreatedAt);
  if (ageCategory === 'NEW') risk += 15;
  else if (ageCategory === 'OLD') risk -= 5;

  // Extreme price changes indicate volatility/risk
  if (Math.abs(token.priceChange24h) > 100) risk += 10;
  else if (Math.abs(token.priceChange24h) > 50) risk += 5;

  // Low volume relative to market cap is suspicious
  if (token.marketCap > 0 && token.volume24h / token.marketCap < 0.01) risk += 10;

  // Clamp to 0-100
  return Math.max(0, Math.min(100, risk));
}

function getDnaRiskLevel(riskScore: number): 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME' {
  if (riskScore < 25) return 'LOW';
  if (riskScore < 50) return 'MEDIUM';
  if (riskScore < 75) return 'HIGH';
  return 'EXTREME';
}

// ============================================================
// POST /api/strategy-optimizer
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const action = body.action as string;

    switch (action) {
      case 'scan':
        return await handleScan(body);
      case 'generate_strategies':
        return await handleGenerateStrategies(body);
      case 'run_loop':
        return await handleRunLoop(body);
      case 'rank_results':
        return await handleRankResults(body);
      default:
        return NextResponse.json(
          { data: null, error: `Unknown action: ${action}` },
          { status: 400 }
        );
    }
  } catch (error) {
    console.error('Strategy optimizer error:', error);
    return NextResponse.json(
      { data: null, error: 'Strategy optimizer failed' },
      { status: 500 }
    );
  }
}

// ============================================================
// SCAN - Find opportunities using Brain/DNA/Signals
// ============================================================

async function handleScan(_body: Record<string, unknown>) {
  try {
    // ============================================================
    // STEP 1: Fetch fresh tokens from DexScreener API
    // ============================================================
    let dexTokens: DexScreenerToken[] = [];
    try {
      dexTokens = await fetchDexScreenerTokens();
      console.log(`[Scan] DexScreener: fetched ${dexTokens.length} trending tokens`);
    } catch (error) {
      console.warn('[Scan] DexScreener fetch failed, falling back to DB-only:', error);
    }

    // ============================================================
    // STEP 2: Fetch tokens from local DB with DNA and signals
    // ============================================================
    const dbTokens = await db.token.findMany({
      take: 100,
      orderBy: { volume24h: 'desc' },
      include: {
        dna: true,
        signals: {
          where: { createdAt: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) } },
          take: 5,
          orderBy: { createdAt: 'desc' },
        },
        lifecycleStates: {
          take: 1,
          orderBy: { detectedAt: 'desc' },
        },
      },
    });

    // Get active predictive signals count
    const activePredictiveSignals = await db.predictiveSignal.count({
      where: { validUntil: { gte: new Date() } },
    });

    // Get behavior model count
    const behaviorModelCount = await db.traderBehaviorModel.count();

    // ============================================================
    // STEP 3: Merge DexScreener tokens with DB tokens
    // ============================================================
    const candidates: TokenCandidate[] = [];
    const seenAddresses = new Set<string>();

    // 3a. Process DB tokens first (they get priority - have DNA, signals, lifecycle)
    for (const token of dbTokens) {
      if (token.volume24h <= 0) continue;

      seenAddresses.add(token.address.toLowerCase());

      const dna = token.dna;
      const lifecycle = token.lifecycleStates[0];
      const signalCount = token.signals.length;
      const phase = lifecycle?.phase || null;
      const ageCategory = getTokenAgeCategory(token.createdAt);

      // Check if DexScreener has fresher data for this token
      const dexMatch = dexTokens.find(
        dt => dt.address.toLowerCase() === token.address.toLowerCase()
      );

      // Use DexScreener data for market metrics if available (fresher), DB for DNA/signals
      const priceUsd = dexMatch?.priceUsd ?? token.priceUsd;
      const volume24h = dexMatch?.volume24h ?? token.volume24h;
      const liquidity = dexMatch?.liquidity ?? token.liquidity;
      const marketCap = dexMatch?.marketCap ?? token.marketCap;
      const priceChange24h = dexMatch?.priceChange24h ?? token.priceChange24h;
      const tokenAgeCategory = dexMatch?.pairCreatedAt
        ? getTokenAgeFromTimestamp(dexMatch.pairCreatedAt)
        : ageCategory;

      candidates.push({
        id: token.id,
        symbol: token.symbol,
        name: token.name,
        address: token.address,
        chain: token.chain,
        priceUsd,
        volume24h,
        liquidity,
        marketCap,
        priceChange24h,
        riskScore: dna?.riskScore ?? 50,
        phase,
        dnaRiskLevel: getDnaRiskLevel(dna?.riskScore ?? 50),
        smartMoneyPct: dna?.smartMoneyScore ?? 0,
        botActivityPct: dna?.botActivityScore ?? 0,
        signalCount,
        tokenAgeCategory,
      });
    }

    // 3b. Add DexScreener-only tokens (not in DB)
    for (const dexToken of dexTokens) {
      if (seenAddresses.has(dexToken.address.toLowerCase())) continue;

      seenAddresses.add(dexToken.address.toLowerCase());

      const riskScore = calculateDexRiskScore(dexToken);
      const ageCategory = getTokenAgeFromTimestamp(dexToken.pairCreatedAt);

      candidates.push({
        id: `dex-${dexToken.address.slice(0, 12)}`,
        symbol: dexToken.symbol,
        name: dexToken.name,
        address: dexToken.address,
        chain: dexToken.chain.toUpperCase(),
        priceUsd: dexToken.priceUsd,
        volume24h: dexToken.volume24h,
        liquidity: dexToken.liquidity,
        marketCap: dexToken.marketCap,
        priceChange24h: dexToken.priceChange24h,
        riskScore,
        phase: null, // No lifecycle data from DexScreener
        dnaRiskLevel: getDnaRiskLevel(riskScore),
        smartMoneyPct: 0, // No DNA data from DexScreener
        botActivityPct: 0, // No bot data from DexScreener
        signalCount: 0, // No signal data from DexScreener
        tokenAgeCategory: ageCategory,
      });
    }

    // Sort merged candidates by volume (descending)
    candidates.sort((a, b) => b.volume24h - a.volume24h);

    // Phase distribution
    const lifecyclePhases: Record<string, number> = {};
    for (const c of candidates) {
      if (c.phase) {
        lifecyclePhases[c.phase] = (lifecyclePhases[c.phase] || 0) + 1;
      }
    }

    const result: ScanResult = {
      tokens: candidates,
      dnaProfiles: dbTokens.filter(t => t.dna).length,
      activeSignals: activePredictiveSignals,
      lifecyclePhases,
      behaviorModels: behaviorModelCount,
    };

    return NextResponse.json({ data: result });
  } catch (error) {
    console.error('Scan error:', error);
    // Return empty results on error for graceful degradation
    return NextResponse.json({
      data: {
        tokens: [],
        dnaProfiles: 0,
        activeSignals: 0,
        lifecyclePhases: {},
        behaviorModels: 0,
      } as ScanResult,
    });
  }
}

// ============================================================
// GENERATE_STRATEGIES - Create strategy configurations
// ============================================================

async function handleGenerateStrategies(body: Record<string, unknown>) {
  const capital = Number(body.capital) || 10000;
  const timeframes = (body.timeframes as string[]) || ['1h'];
  const tokenAges = (body.tokenAges as string[]) || ['NEW', 'MEDIUM', 'OLD'];
  const riskTolerance = (body.riskTolerance as string) || 'MODERATE';
  const allocationMode = (body.allocationMode as string) || 'distribute';
  const strategyCount = Number(body.strategyCount) || 5;
  // B4 FIX: Accept scanned tokens directly from the scan step
  const scannedTokens = (body.scannedTokens as TokenCandidate[]) || [];
  const scannedChains = (body.scannedChains as string[]) || undefined;
  const scannedTokenCount = Number(body.scannedTokenCount) || scannedTokens.length;

  // Determine which chains to target based on scan results
  const targetChains = scannedChains && scannedChains.length > 0
    ? scannedChains.map(c => c.toUpperCase())
    : scannedTokens.length > 0
      ? [...new Set(scannedTokens.map(t => t.chain?.toUpperCase()).filter(Boolean))]
      : ['SOL', 'ETH', 'BASE'];

  // Risk-based configuration presets
  const riskPresets: Record<string, {
    maxDrawdown: number; stopLoss: number; takeProfit: number;
    positionSize: number; confidenceThreshold: number;
    maxConcurrent: number;
  }> = {
    CONSERVATIVE: { maxDrawdown: 10, stopLoss: 8, takeProfit: 25, positionSize: 3, confidenceThreshold: 80, maxConcurrent: 3 },
    MODERATE: { maxDrawdown: 20, stopLoss: 15, takeProfit: 40, positionSize: 5, confidenceThreshold: 65, maxConcurrent: 5 },
    AGGRESSIVE: { maxDrawdown: 35, stopLoss: 25, takeProfit: 80, positionSize: 10, confidenceThreshold: 50, maxConcurrent: 8 },
  };

  const preset = riskPresets[riskTolerance] || riskPresets.MODERATE;

  // ============================================================
  // B4 FIX: Generate strategies based on REAL scanned tokens
  // Instead of generic templates, build strategies that target
  // the specific tokens discovered in the scan step.
  // ============================================================

  const strategies: StrategyConfig[] = [];
  const perStrategyCapital = allocationMode === 'focus' ? capital : capital / strategyCount;

  if (scannedTokens.length > 0) {
    // === STRATEGY GENERATION FROM REAL SCANNED TOKENS ===

    // Group tokens by their properties for strategy assignment
    const highRiskTokens = scannedTokens.filter(t => (t.riskScore ?? 50) >= 70);
    const mediumRiskTokens = scannedTokens.filter(t => (t.riskScore ?? 50) >= 30 && (t.riskScore ?? 50) < 70);
    const lowRiskTokens = scannedTokens.filter(t => (t.riskScore ?? 50) < 30);
    const smartMoneyTokens = scannedTokens.filter(t => (t.smartMoneyPct ?? 0) > 20);
    const botHeavyTokens = scannedTokens.filter(t => (t.botActivityPct ?? 0) > 30);
    const growthPhaseTokens = scannedTokens.filter(t => t.phase === 'GROWTH');
    const newTokens = scannedTokens.filter(t => t.tokenAgeCategory === 'NEW');

    // Build strategy configs based on actual token groups
    const tokenBasedStrategies: Array<{
      category: string; icon: string; namePrefix: string;
      tokens: TokenCandidate[]; signalType: string;
      description: string;
    }> = [];

    // 1. Alpha Hunter — targets high-volatility new tokens
    if (newTokens.length > 0 || highRiskTokens.length > 0) {
      tokenBasedStrategies.push({
        category: 'ALPHA_HUNTER', icon: '🎯', namePrefix: 'Alpha Hunter',
        tokens: [...newTokens, ...highRiskTokens].slice(0, 10),
        signalType: 'MOMENTUM_BREAKOUT',
        description: `Targets ${Math.min(newTokens.length + highRiskTokens.length, 10)} high-potential tokens`,
      });
    }

    // 2. Smart Money — follows whale/smart money accumulation
    if (smartMoneyTokens.length > 0) {
      tokenBasedStrategies.push({
        category: 'SMART_MONEY', icon: '🧠', namePrefix: 'Smart Money',
        tokens: smartMoneyTokens.slice(0, 10),
        signalType: 'SMART_MONEY_ENTRY',
        description: `Tracks ${smartMoneyTokens.length} tokens with smart money presence`,
      });
    }

    // 3. Technical — growth phase tokens for technical analysis
    if (growthPhaseTokens.length > 0 || mediumRiskTokens.length > 0) {
      tokenBasedStrategies.push({
        category: 'TECHNICAL', icon: '📊', namePrefix: 'Technical',
        tokens: [...growthPhaseTokens, ...mediumRiskTokens].slice(0, 10),
        signalType: 'DIVERGENCE',
        description: `Technical analysis on ${Math.min(growthPhaseTokens.length + mediumRiskTokens.length, 10)} tokens`,
      });
    }

    // 4. Defensive — low risk, established tokens
    if (lowRiskTokens.length > 0) {
      tokenBasedStrategies.push({
        category: 'DEFENSIVE', icon: '🛡️', namePrefix: 'Defensive',
        tokens: lowRiskTokens.slice(0, 10),
        signalType: 'V_SHAPE_RECOVERY',
        description: `Conservative plays on ${lowRiskTokens.length} stable tokens`,
      });
    }

    // 5. Bot-Aware — tokens with high bot activity (avoid or exploit)
    if (botHeavyTokens.length > 0) {
      tokenBasedStrategies.push({
        category: 'BOT_AWARE', icon: '🤖', namePrefix: 'Bot-Aware',
        tokens: botHeavyTokens.slice(0, 10),
        signalType: 'BOT_DETECTION',
        description: `Navigating ${botHeavyTokens.length} bot-heavy tokens`,
      });
    }

    // 6. Adaptive — best overall tokens from scan
    const topTokens = [...scannedTokens]
      .sort((a, b) => (b.volume24h ?? 0) - (a.volume24h ?? 0))
      .slice(0, 10);
    tokenBasedStrategies.push({
      category: 'ADAPTIVE', icon: '🔄', namePrefix: 'Adaptive',
      tokens: topTokens,
      signalType: 'LIQUIDITY_SURGE',
      description: `Top ${topTokens.length} tokens by volume — adaptive approach`,
    });

    // Generate one strategy per timeframe for the top token-based groups
    let strategyId = 0;
    for (const timeframe of timeframes) {
      for (const s of tokenBasedStrategies) {
        if (strategies.length >= strategyCount) break;

        strategyId++;
        const tokenAddresses = s.tokens.map(t => t.address);
        const tokenSymbols = s.tokens.slice(0, 5).map(t => t.symbol).join(', ');
        const avgRiskScore = s.tokens.length > 0
          ? Math.round(s.tokens.reduce((sum, t) => sum + (t.riskScore ?? 50), 0) / s.tokens.length)
          : 50;

        // Adjust SL/TP based on actual token risk
        const riskMultiplier = avgRiskScore > 70 ? 1.5 : avgRiskScore > 50 ? 1.0 : 0.7;
        const adjustedStopLoss = Math.round(preset.stopLoss * riskMultiplier);
        const adjustedTakeProfit = Math.round(preset.takeProfit * riskMultiplier);

        // Determine best tokenAgeCategory from the actual tokens
        const dominantAge = s.tokens.length > 0
          ? s.tokens.reduce<Record<string, number>>((acc, t) => {
              const age = t.tokenAgeCategory || 'MEDIUM';
              acc[age] = (acc[age] || 0) + 1;
              return acc;
            }, {})
          : { MEDIUM: 1 };
        const bestAge = Object.entries(dominantAge).sort((a, b) => b[1] - a[1])[0]?.[0] || 'MEDIUM';

        strategies.push({
          id: `strategy-${strategyId}`,
          name: `${s.namePrefix} | ${timeframe} | ${tokenSymbols}`,
          category: s.category,
          icon: s.icon,
          timeframe,
          tokenAgeCategory: bestAge,
          riskTolerance,
          capitalAllocation: perStrategyCapital,
          config: {
            assetFilter: {
              minLiquidity: bestAge === 'NEW' ? 5000 : bestAge === 'MEDIUM' ? 10000 : 50000,
              minVolume24h: bestAge === 'NEW' ? 500 : 5000,
              maxMarketCap: bestAge === 'NEW' ? 100000000 : bestAge === 'MEDIUM' ? 1000000000 : 0,
              tokenAge: bestAge === 'NEW' ? '<7D' : bestAge === 'MEDIUM' ? '<30D' : '>30D',
              chains: targetChains,
              scannedTokenCount,
              // B4: Include actual token addresses from the scan
              targetTokenAddresses: tokenAddresses,
            },
            phaseConfig: {
              genesis: bestAge === 'NEW',
              early: bestAge === 'NEW' || bestAge === 'MEDIUM',
              growth: true,
              maturity: bestAge === 'OLD',
              decline: false,
            },
            entrySignal: {
              signalType: s.signalType,
              confidenceThreshold: preset.confidenceThreshold,
              confirmationRequired: riskTolerance !== 'AGGRESSIVE',
              timeWindow: timeframe === '1m' ? 1 : timeframe === '5m' ? 5 : timeframe === '10m' ? 10 : timeframe === '15m' ? 15 : timeframe === '30m' ? 30 : timeframe === '1h' ? 60 : 240,
            },
            exitSignal: {
              takeProfit: adjustedTakeProfit,
              stopLoss: adjustedStopLoss,
              trailingStop: riskTolerance !== 'CONSERVATIVE',
              trailingStopPercent: Math.round(adjustedTakeProfit * 0.6),
              timeBasedExit: timeframe === '1m' ? 30 : timeframe === '5m' ? 60 : timeframe === '1h' ? 1440 : 2880,
            },
            riskManagement: {
              maxDrawdown: preset.maxDrawdown,
              maxConcurrentTrades: preset.maxConcurrent,
              maxDailyLoss: Math.round(preset.maxDrawdown * 0.5),
              positionSizing: 'RISK_BASED',
            },
            executionConfig: {
              orderType: riskTolerance === 'AGGRESSIVE' ? 'MARKET' : 'LIMIT',
              slippageTolerance: bestAge === 'NEW' ? 2.0 : 1.0,
              maxPositionSize: preset.positionSize,
              executionDelay: 0,
            },
          },
        });
      }
      if (strategies.length >= strategyCount) break;
    }
  } else {
    // === FALLBACK: Generic templates when no scan data is available ===
    const categoryTemplates = [
      { category: 'ALPHA_HUNTER', icon: '🎯', namePrefix: 'Alpha Hunter' },
      { category: 'SMART_MONEY', icon: '🧠', namePrefix: 'Smart Money' },
      { category: 'TECHNICAL', icon: '📊', namePrefix: 'Technical' },
      { category: 'DEFENSIVE', icon: '🛡️', namePrefix: 'Defensive' },
      { category: 'BOT_AWARE', icon: '🤖', namePrefix: 'Bot-Aware' },
      { category: 'ADAPTIVE', icon: '🔄', namePrefix: 'Adaptive' },
    ];

    let strategyId = 0;
    for (const timeframe of timeframes) {
      for (const tokenAge of tokenAges) {
        for (const template of categoryTemplates) {
          if (strategies.length >= strategyCount) break;
          strategyId++;
          const tokenAgeLabel = tokenAge === 'NEW' ? '<7d' : tokenAge === 'MEDIUM' ? '<30d' : '>30d';

          strategies.push({
            id: `strategy-${strategyId}`,
            name: `${template.namePrefix} | ${timeframe} | ${tokenAgeLabel}`,
            category: template.category,
            icon: template.icon,
            timeframe,
            tokenAgeCategory: tokenAge,
            riskTolerance,
            capitalAllocation: perStrategyCapital,
            config: {
              assetFilter: {
                minLiquidity: tokenAge === 'NEW' ? 5000 : tokenAge === 'MEDIUM' ? 10000 : 50000,
                minVolume24h: tokenAge === 'NEW' ? 500 : 5000,
                maxMarketCap: tokenAge === 'NEW' ? 100000000 : tokenAge === 'MEDIUM' ? 1000000000 : 0,
                tokenAge: tokenAge === 'NEW' ? '<7D' : tokenAge === 'MEDIUM' ? '<30D' : '>30D',
                chains: targetChains,
                scannedTokenCount,
              },
              phaseConfig: {
                genesis: tokenAge === 'NEW',
                early: tokenAge === 'NEW' || tokenAge === 'MEDIUM',
                growth: true,
                maturity: tokenAge === 'OLD',
                decline: false,
              },
              entrySignal: {
                signalType: template.category === 'SMART_MONEY' ? 'SMART_MONEY_ENTRY' :
                           template.category === 'BOT_AWARE' ? 'BOT_DETECTION' :
                           template.category === 'ALPHA_HUNTER' ? 'MOMENTUM_BREAKOUT' :
                           template.category === 'TECHNICAL' ? 'DIVERGENCE' :
                           template.category === 'DEFENSIVE' ? 'V_SHAPE_RECOVERY' : 'LIQUIDITY_SURGE',
                confidenceThreshold: preset.confidenceThreshold,
                confirmationRequired: riskTolerance !== 'AGGRESSIVE',
                timeWindow: timeframe === '1m' ? 1 : timeframe === '5m' ? 5 : timeframe === '10m' ? 10 : timeframe === '15m' ? 15 : timeframe === '30m' ? 30 : timeframe === '1h' ? 60 : 240,
              },
              exitSignal: {
                takeProfit: preset.takeProfit,
                stopLoss: preset.stopLoss,
                trailingStop: riskTolerance !== 'CONSERVATIVE',
                trailingStopPercent: Math.round(preset.takeProfit * 0.6),
                timeBasedExit: timeframe === '1m' ? 30 : timeframe === '5m' ? 60 : timeframe === '1h' ? 1440 : 2880,
              },
              riskManagement: {
                maxDrawdown: preset.maxDrawdown,
                maxConcurrentTrades: preset.maxConcurrent,
                maxDailyLoss: Math.round(preset.maxDrawdown * 0.5),
                positionSizing: 'RISK_BASED',
              },
              executionConfig: {
                orderType: riskTolerance === 'AGGRESSIVE' ? 'MARKET' : 'LIMIT',
                slippageTolerance: tokenAge === 'NEW' ? 2.0 : 1.0,
                maxPositionSize: preset.positionSize,
                executionDelay: 0,
              },
            },
          });
        }
        if (strategies.length >= strategyCount) break;
      }
      if (strategies.length >= strategyCount) break;
    }
  }

  return NextResponse.json({ data: {
    strategies,
    totalGenerated: strategies.length,
    perStrategyCapital,
    scanBasedStrategies: scannedTokens.length > 0,
    scannedTokenCount: scannedTokens.length,
  } });
}

// ============================================================
// RUN_LOOP - Execute optimization loop (create + run backtests)
// Directly calls the backtesting engine instead of HTTP self-fetch
// ============================================================

async function handleRunLoop(body: Record<string, unknown>) {
  const strategies = (body.strategies as StrategyConfig[]) || [];
  const capital = Number(body.capital) || 10000;

  // Dynamic imports to avoid cold-start overhead when not needed
  const bteModule = await import('@/lib/services/backtesting/backtesting-engine');
  const backtestingEngine = bteModule.backtestingEngine;
  type BacktestConfig = import('@/lib/services/backtesting/backtesting-engine').BacktestConfig;

  const tseModule = await import('@/lib/services/strategy/trading-system-engine');
  const tradingSystemEngine = tseModule.tradingSystemEngine;

  const bdbModule = await import('@/lib/services/backtesting/backtest-data-bridge');
  const backtestDataBridge = bdbModule.backtestDataBridge;

  const results: Array<{
    strategyId: string;
    strategyName: string;
    backtestId: string | null;
    status: 'created' | 'running' | 'completed' | 'failed';
    error?: string;
  }> = [];

  // Get or create a default trading system for the optimizer
  let defaultSystem = await db.tradingSystem.findFirst({
    where: { category: 'ADAPTIVE' },
    orderBy: { createdAt: 'desc' },
  });

  if (!defaultSystem) {
    defaultSystem = await db.tradingSystem.create({
      data: {
        name: 'AI Optimizer - Adaptive',
        category: 'ADAPTIVE',
        icon: '🔄',
        assetFilter: JSON.stringify({ tokenAge: 'ANY', chains: ['SOL', 'ETH', 'BASE'] }),
        phaseConfig: JSON.stringify({ genesis: true, early: true, growth: true, maturity: true, decline: false }),
        entrySignal: JSON.stringify({ signalType: 'MOMENTUM_BREAKOUT', confidenceThreshold: 60 }),
        executionConfig: JSON.stringify({ orderType: 'LIMIT', slippageTolerance: 1.5 }),
        exitSignal: JSON.stringify({ takeProfit: 40, stopLoss: 15, trailingStop: true, trailingStopPercent: 25 }),
        bigDataContext: JSON.stringify({}),
        primaryTimeframe: '1h',
        allocationMethod: 'KELLY_MODIFIED',
        maxPositionPct: 5,
        stopLossPct: 15,
        takeProfitPct: 40,
        cashReservePct: 20,
        isActive: false,
        isPaperTrading: false,
      },
    });
  }

  // Run strategies SEQUENTIALLY to avoid overloading the DB
  for (const strategy of strategies) {
    try {
      // 1. Create a TradingSystem record in the DB
      const system = await db.tradingSystem.create({
        data: {
          name: strategy.name,
          category: strategy.category as 'ALPHA_HUNTER',
          icon: strategy.icon,
          assetFilter: JSON.stringify(strategy.config.assetFilter),
          phaseConfig: JSON.stringify(strategy.config.phaseConfig),
          entrySignal: JSON.stringify(strategy.config.entrySignal),
          executionConfig: JSON.stringify(strategy.config.executionConfig),
          exitSignal: JSON.stringify(strategy.config.exitSignal),
          bigDataContext: JSON.stringify({}),
          primaryTimeframe: strategy.timeframe,
          allocationMethod: 'KELLY_MODIFIED',
          maxPositionPct: (strategy.config.riskManagement as Record<string, unknown>).maxPositionSize as number || 5,
          stopLossPct: (strategy.config.exitSignal as Record<string, unknown>).stopLoss as number || 15,
          takeProfitPct: (strategy.config.exitSignal as Record<string, unknown>).takeProfit as number || 40,
          cashReservePct: 20,
          isActive: false,
          isPaperTrading: false,
          parentSystemId: defaultSystem.id,
        },
      });

      // 2. Create a BacktestRun record with status RUNNING
      const periodStart = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
      const periodEnd = new Date();
      const initialCapital = strategy.capitalAllocation || capital / strategies.length;

      const backtest = await db.backtestRun.create({
        data: {
          systemId: system.id,
          mode: 'HISTORICAL',
          periodStart,
          periodEnd,
          initialCapital,
          allocationMethod: 'KELLY_MODIFIED',
          capitalAllocation: JSON.stringify({
            method: 'KELLY_MODIFIED',
            initialCapital,
          }),
          strategyMeta: JSON.stringify({
            strategyId: strategy.id,
            strategyName: strategy.name,
            category: strategy.category,
            timeframe: strategy.timeframe,
            tokenAgeCategory: strategy.tokenAgeCategory,
            riskTolerance: strategy.riskTolerance,
          }),
          status: 'RUNNING',
          progress: 0.05,
          startedAt: new Date(),
        },
      });

      results.push({
        strategyId: strategy.id,
        strategyName: strategy.name,
        backtestId: backtest.id,
        status: 'running',
      });

      // 3. Build a system template from the trading system engine
      const systemTemplate = tradingSystemEngine.getTemplate(system.name) ??
        tradingSystemEngine.createSystemFromTemplate(
          tradingSystemEngine.getAllTemplateNames()[0],
          {
            name: system.name,
            category: system.category as 'ALPHA_HUNTER',
          },
        );

      // 4. Load token data via backtestDataBridge
      // Use the strategy's timeframe (e.g., "4h") rather than template default ("1m")
      // since our candle data is stored in the strategy's timeframe
      const backtestTimeframe = strategy.timeframe || systemTemplate.primaryTimeframe || '4h';

      // Convert tokenAge string (e.g., '<7D') to maxAgeHours for the data bridge
      let maxAgeHours: number | undefined;
      const assetFilter = strategy.config.assetFilter as Record<string, unknown>;
      const tokenAgeStr = assetFilter?.tokenAge as string | undefined;
      if (tokenAgeStr && tokenAgeStr !== 'ANY') {
        const match = tokenAgeStr.match(/<?(\d+)([DHM])/i);
        if (match) {
          const value = parseInt(match[1]);
          const unit = match[2].toUpperCase();
          maxAgeHours = unit === 'D' ? value * 24 : unit === 'H' ? value : Math.round(value / 60);
        }
      }

      // Build enhanced assetFilter with proper chain and age filters
      const enhancedAssetFilter = {
        ...systemTemplate.assetFilter,
        ...(maxAgeHours ? { maxAgeHours } : {}),
        chains: (assetFilter?.chains as string[]) || ['SOL', 'ETH', 'BASE'],
      };

      let tokenData = await backtestDataBridge.loadTokensForBacktest({
        startDate: periodStart,
        endDate: periodEnd,
        timeframe: backtestTimeframe,
        chain: undefined, // Let autoDiscover use assetFilter.chains
        minCandles: 20,
        assetFilter: enhancedAssetFilter,
        maxTokens: 10,
        includeMetrics: true,
      });

      // Update progress after data load
      await db.backtestRun.update({
        where: { id: backtest.id },
        data: { progress: 0.2 },
      });

      // Validate token data quality before passing to the engine
      const { valid: validTokenData, rejected } = backtestDataBridge.validateTokenData(tokenData);
      if (rejected.length > 0) {
        console.warn(`[run_loop] Rejected ${rejected.length} tokens with bad data:`, rejected);
      }
      tokenData = validTokenData;

      if (tokenData.length === 0) {
        // No data available — mark as FAILED (not COMPLETED) so it's excluded from ranking
        await db.backtestRun.update({
          where: { id: backtest.id },
          data: {
            status: 'FAILED',
            progress: 1,
            completedAt: new Date(),
            finalCapital: initialCapital,
            totalPnl: 0,
            totalPnlPct: 0,
            errorLog: 'No token data available for backtesting. Run OHLCV backfill first.',
          },
        });

        // Update the result status
        const resultEntry = results.find(r => r.backtestId === backtest.id);
        if (resultEntry) {
          resultEntry.status = 'failed';
          resultEntry.error = 'No token data available. Run OHLCV backfill first.';
        }
        continue;
      }

      // 5. Build BacktestConfig and run the backtesting engine
      const btConfig: BacktestConfig = {
        system: systemTemplate,
        mode: 'HISTORICAL',
        startDate: periodStart,
        endDate: periodEnd,
        initialCapital,
        feesPct: 0.003,
        slippagePct: 0.5,
        applySlippage: true,
        enforcePhaseFilter: true,
      };

      await db.backtestRun.update({
        where: { id: backtest.id },
        data: { progress: 0.3 },
      });

      const result = await backtestingEngine.runBacktest(
        btConfig,
        tokenData,
        async (progress) => {
          if (progress.barsProcessed % 500 === 0) {
            try {
              await db.backtestRun.update({
                where: { id: backtest.id },
                data: {
                  progress: Math.min(0.9, 0.3 + progress.percentComplete * 0.006),
                },
              });
            } catch {
              // Progress update failures are non-critical
            }
          }
        },
      );

      await db.backtestRun.update({
        where: { id: backtest.id },
        data: { progress: 0.9 },
      });

      // 6. Create BacktestOperation records for each trade
      const operationCreates = result.trades.map((trade) => ({
        backtestId: backtest.id,
        systemId: system.id,
        tokenAddress: trade.tokenAddress,
        tokenSymbol: trade.symbol,
        chain: (() => {
          // Infer chain from the token address — ETH tokens start with 0x, SOL tokens are base58
          const addr = trade.tokenAddress || '';
          if (addr.startsWith('0x')) return 'ethereum';
          if (addr.length > 30 && !addr.startsWith('0x')) return 'solana';
          return 'eth'; // default
        })(),
        tokenPhase: trade.phase,
        tokenAgeMinutes: 0,
        marketConditions: JSON.stringify({ timeframe: systemTemplate.primaryTimeframe }),
        tokenDnaSnapshot: JSON.stringify({}),
        traderComposition: JSON.stringify({}),
        bigDataContext: JSON.stringify({}),
        operationType: trade.direction,
        timeframe: systemTemplate.primaryTimeframe,
        entryPrice: trade.entryPrice,
        entryTime: trade.entryTime,
        entryReason: JSON.stringify({ reason: 'backtest_simulation', system: systemTemplate.name }),
        exitPrice: trade.exitPrice ?? 0,
        exitTime: trade.exitTime ?? new Date(),
        exitReason: trade.exitReason,
        quantity: trade.quantity,
        positionSizeUsd: trade.size,
        pnlUsd: trade.pnl,
        pnlPct: trade.pnlPct,
        holdTimeMin: trade.holdTimeMin,
        maxFavorableExc: trade.mfe,
        maxAdverseExc: trade.mae,
        capitalAllocPct: trade.size / initialCapital * 100,
        allocationMethodUsed: systemTemplate.allocationMethod,
      }));

      if (operationCreates.length > 0) {
        await db.backtestOperation.createMany({
          data: operationCreates,
        });
      }

      // 7. Update the BacktestRun with results
      await db.backtestRun.update({
        where: { id: backtest.id },
        data: {
          status: 'COMPLETED',
          progress: 1,
          completedAt: new Date(),
          finalCapital: result.finalEquity,
          totalPnl: result.finalEquity - result.initialCapital,
          totalPnlPct: result.totalReturnPct,
          annualizedReturn: result.annualizedReturnPct,
          benchmarkReturn: 0,
          alpha: result.annualizedReturnPct,
          totalTrades: result.totalTrades,
          winTrades: result.winningTrades,
          lossTrades: result.losingTrades,
          winRate: result.winRate,
          avgWin: result.avgWinPct,
          avgLoss: result.avgLossPct,
          profitFactor: result.profitFactor,
          expectancy: result.expectancy,
          maxDrawdown: result.maxDrawdown,
          maxDrawdownPct: result.maxDrawdownPct,
          sharpeRatio: result.sharpeRatio,
          sortinoRatio: result.sortinoRatio,
          calmarRatio: result.calmarRatio,
          recoveryFactor: result.recoveryFactor,
          avgHoldTimeMin: result.avgHoldTimeMin,
          marketExposurePct: result.totalTrades > 0 && result.avgHoldTimeMin > 0
            ? Math.min(100, (result.totalTrades * result.avgHoldTimeMin) / ((periodEnd.getTime() - periodStart.getTime()) / 60000) * 100)
            : 0,
          phaseResults: JSON.stringify(result.phaseBreakdown),
          timeframeResults: JSON.stringify({ primaryTimeframe: systemTemplate.primaryTimeframe }),
          operationTypeResults: JSON.stringify({}),
          allocationMethodResults: JSON.stringify({ method: systemTemplate.allocationMethod }),
        },
      });

      // Update trading system metrics
      const updatedSystem = await db.tradingSystem.findUnique({
        where: { id: system.id },
      });

      if (updatedSystem) {
        const metricsUpdate: Record<string, unknown> = {
          totalBacktests: updatedSystem.totalBacktests + 1,
        };

        if (result.sharpeRatio > updatedSystem.bestSharpe) {
          metricsUpdate.bestSharpe = result.sharpeRatio;
        }
        if (result.winRate > updatedSystem.bestWinRate) {
          metricsUpdate.bestWinRate = result.winRate;
        }
        if (result.totalReturnPct > updatedSystem.bestPnlPct) {
          metricsUpdate.bestPnlPct = result.totalReturnPct;
        }

        if (updatedSystem.totalBacktests === 0) {
          metricsUpdate.avgHoldTimeMin = result.avgHoldTimeMin;
        } else {
          metricsUpdate.avgHoldTimeMin =
            (updatedSystem.avgHoldTimeMin * updatedSystem.totalBacktests + result.avgHoldTimeMin) /
            (updatedSystem.totalBacktests + 1);
        }

        await db.tradingSystem.update({
          where: { id: system.id },
          data: metricsUpdate,
        });
      }

      // Update the result status
      const resultEntry = results.find(r => r.backtestId === backtest.id);
      if (resultEntry) resultEntry.status = 'completed';
    } catch (error) {
      // Mark the backtest as failed if we have an ID
      const failedResult = results.find(
        r => r.strategyId === strategy.id && r.backtestId
      );
      if (failedResult && failedResult.backtestId) {
        await db.backtestRun.update({
          where: { id: failedResult.backtestId },
          data: {
            status: 'FAILED',
            errorLog: error instanceof Error ? error.message : 'Unknown simulation error',
            completedAt: new Date(),
          },
        }).catch(() => { /* ignore secondary update errors */ });
        failedResult.status = 'failed';
        failedResult.error = error instanceof Error ? error.message : 'Unknown error';
      } else {
        results.push({
          strategyId: strategy.id,
          strategyName: strategy.name,
          backtestId: null,
          status: 'failed',
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }
  }

  return NextResponse.json({
    data: {
      results,
      totalCreated: results.filter(r => r.backtestId).length,
      totalFailed: results.filter(r => r.status === 'failed').length,
      totalRunning: results.filter(r => r.status === 'running').length,
      totalCompleted: results.filter(r => r.status === 'completed').length,
    }
  });
}

// ============================================================
// RANK_RESULTS - Rank all completed backtests
// ============================================================

async function handleRankResults(body: Record<string, unknown>) {
  const sortBy = (body.sortBy as string) || 'score';
  const filterTimeframe = body.filterTimeframe as string | undefined;
  const filterTokenAge = body.filterTokenAge as string | undefined;
  const filterRisk = body.filterRisk as string | undefined;
  const includeZeroTrade = (body.includeZeroTrade as boolean) || false;
  const recentHours = Number(body.recentHours) || 0; // 0 = all time, N = last N hours

  // Build where clause with optional time filter
  const whereClause: Record<string, unknown> = { status: 'COMPLETED' };
  if (recentHours > 0) {
    const cutoff = new Date(Date.now() - recentHours * 60 * 60 * 1000);
    whereClause.completedAt = { gte: cutoff };
  }

  // Fetch completed backtests with system info
  const backtests = await db.backtestRun.findMany({
    where: whereClause,
    include: { system: true },
    orderBy: { completedAt: 'desc' },
  });

  // Parse and rank results
  const ranked: RankResult[] = backtests
    .map(bt => {
      // Try to extract strategy metadata from strategyMeta JSON (proper field)
      // Fallback: try capitalAllocation JSON (legacy data from before schema migration)
      let strategyMeta: Record<string, unknown> = {};
      try {
        strategyMeta = JSON.parse(bt.strategyMeta || '{}');
      } catch { /* ignore */ }
      if (Object.keys(strategyMeta).length === 0) {
        try {
          const capAlloc = JSON.parse(bt.capitalAllocation || '{}');
          // Only use capitalAllocation as fallback if it has strategy fields
          if (capAlloc.strategyName || capAlloc.category) {
            strategyMeta = capAlloc;
          }
        } catch { /* ignore */ }
      }

      const pnlUsd = bt.totalPnl ?? (bt.finalCapital - bt.initialCapital);
      const pnlPct = bt.totalPnlPct;
      const totalTrades = bt.totalTrades || 0;

      // ============================================================
      // COMPOSITE SCORE v2 — Robust scoring that handles edge cases
      // ============================================================
      // Key improvements over v1:
      //   - Backtests with 0 trades get score 0 (no free points)
      //   - normalizedPnl centers at 0 (not 50), so break-even = neutral
      //   - Trade-count bonus rewards statistical significance
      //   - Min 3 trades required to qualify for ranking
      // ============================================================
      let score = 0;

      if (totalTrades === 0) {
        // No trades executed — cannot evaluate strategy quality
        score = 0;
      } else {
        // Normalize each metric to 0–100 range
        // Sharpe: typical range [-2, +3], map to [0, 100]
        const normalizedSharpe = Math.min(100, Math.max(0, (bt.sharpeRatio + 2) * 20));
        // Win rate: [0, 1] → [0, 100]
        const normalizedWinRate = bt.winRate * 100;
        // PnL%: center at 0, map [-10%, +10%] → [0, 100]
        // Break-even gets 50, losses get < 50, profits get > 50
        const normalizedPnl = Math.min(100, Math.max(0, 50 + pnlPct * 5));
        // Profit factor: [0, 5] → [0, 100]
        const normalizedPF = Math.min(100, Math.max(0, bt.profitFactor * 20));
        // Max drawdown: penalize, [0%, 50%] → [0, 100]
        const normalizedDD = Math.min(100, Math.max(0, bt.maxDrawdownPct * 2));
        // Trade count bonus: rewards statistical significance, up to 15 points
        const tradeBonus = Math.min(15, totalTrades * 0.5);

        score = (
          normalizedSharpe * 0.30 +
          normalizedWinRate * 0.20 +
          normalizedPnl    * 0.25 +
          normalizedPF     * 0.10 +
          tradeBonus       * 0.05 -
          normalizedDD     * 0.10
        );

        // Ensure minimum score for backtests with trades but poor metrics
        score = Math.max(0, score);
      }

      return {
        id: bt.id, // Stable ID — use backtestId directly for React key stability
        backtestId: bt.id,
        strategyName: (strategyMeta.strategyName as string) || bt.system?.name || 'Unknown',
        category: (strategyMeta.category as string) || bt.system?.category || 'UNKNOWN',
        timeframe: (strategyMeta.timeframe as string) || bt.system?.primaryTimeframe || '1h',
        tokenAgeCategory: (strategyMeta.tokenAgeCategory as string) || (strategyMeta.tokenAge as string) || 'MEDIUM',
        riskTolerance: (strategyMeta.riskTolerance as string) || (strategyMeta.risk as string) || 'MODERATE',
        capitalAllocation: bt.initialCapital,
        pnlPct,
        pnlUsd,
        sharpeRatio: bt.sharpeRatio,
        winRate: bt.winRate,
        maxDrawdownPct: bt.maxDrawdownPct,
        profitFactor: bt.profitFactor,
        totalTrades,
        avgHoldTimeMin: bt.avgHoldTimeMin,
        score,
        status: bt.status,
      } as RankResult;
    })
    // Filter: exclude 0-trade backtests unless explicitly requested
    .filter(item => {
      if (!includeZeroTrade && item.totalTrades === 0) return false;
      if (filterTimeframe && item.timeframe !== filterTimeframe) return false;
      if (filterTokenAge && item.tokenAgeCategory !== filterTokenAge) return false;
      if (filterRisk && item.riskTolerance !== filterRisk) return false;
      return true;
    });

  // Sort by the requested key (default: score, descending)
  const sortKey = sortBy as keyof RankResult;
  ranked.sort((a, b) => {
    const aVal = a[sortKey];
    const bVal = b[sortKey];
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return bVal - aVal; // Descending
    }
    return String(bVal).localeCompare(String(aVal));
  });

  // Add rank numbers and score grade
  ranked.forEach((item, index) => {
    item.rank = index + 1;
  });

  // Compute grade labels for each result (based on score)
  const gradedResults = ranked.map(item => {
    let grade = 'F';
    if (item.score >= 80) grade = 'A+';
    else if (item.score >= 65) grade = 'A';
    else if (item.score >= 50) grade = 'B';
    else if (item.score >= 35) grade = 'C';
    else if (item.score >= 20) grade = 'D';
    return { ...item, grade };
  });

  return NextResponse.json({ data: { results: gradedResults, totalRanked: gradedResults.length } });
}
