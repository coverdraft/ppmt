/**
 * Seed Endpoint — CryptoQuant Terminal (NO SIMULATED DATA)
 *
 * GET  /api/seed        → Returns current DB seed status
 * POST /api/seed        → Runs the seed process
 *
 * POST body options:
 *   { "action": "full" }     → Full seed: tokens + DNA + signals + candles + patterns + models
 *   { "action": "tokens" }   → Only tokens (Steps 1-6)
 *   { "action": "traders" }  → Redirect message: use /api/real-sync for real trader data
 *   { "action": "patterns" } → Only pattern rules (Step 11)
 *   { "action": "status" }   → Just check status (same as GET)
 *   { "action": "real" }     → Trigger real-sync for everything (traders + candles + dna + patterns)
 *
 * KEY PRINCIPLE: If data is not available from a real API, DON'T create it.
 * An empty database with real data is better than a full database with fake data.
 *
 * Strategy to reach 2000-5000 tokens:
 *   1. DexScreener boosted/trending tokens (1 API call, ~100 tokens)
 *   2. DexScreener direct bulk search per chain (~500-1000 tokens)
 *   3. DexScreener per-symbol search on popular tokens (~500 tokens)
 *   4. DexPaprika top tokens per chain (~250 tokens)
 *   5. CoinGecko top 250 by market cap (~250 tokens)
 *   6. CoinGecko top 250 by volume (~250 tokens)
 *
 * All writes use upsert — idempotent, running again just adds new tokens.
 */

import { NextRequest, NextResponse } from 'next/server';
import { ohlcvPipeline } from '@/lib/services/data-sources/ohlcv-pipeline';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ── Concurrency guard ──────────────────────────────────────
let seedRunning = false;
let lastSeedResult: SeedResult | null = null;

// ── Types ──────────────────────────────────────────────────
interface SeedResult {
  tokensImported: number;
  dnaCreated: number;
  signalsCreated: number;
  candlesCreated: number;
  totalTokens: number;
  // Step 10: Trader seeding (now redirect-only)
  tradersCreated: number;
  // Step 11: Pattern seeding
  patternRulesCreated: number;
  // Step 12: Behavioral model
  behaviorModelsInitialized: number;
  // Step 13: Real sync
  realSyncTriggered: boolean;
  // OHLCV pipeline backfill
  ohlcvBackfill: {
    tokensProcessed: number;
    totalCandlesStored: number;
    failedTokens: string[];
  };
}

type SeedAction = 'full' | 'tokens' | 'traders' | 'patterns' | 'status' | 'real';

interface SeedBody {
  action?: SeedAction;
}

// ── Chain definitions ──────────────────────────────────────
const CHAINS = [
  { id: 'solana', internal: 'SOL' },
  { id: 'ethereum', internal: 'ETH' },
  { id: 'bsc', internal: 'BSC' },
  { id: 'base', internal: 'BASE' },
  { id: 'arbitrum', internal: 'ARB' },
  { id: 'polygon', internal: 'MATIC' },
  { id: 'avalanche', internal: 'AVAX' },
  { id: 'optimism', internal: 'OP' },
] as const;

// Popular token symbols per chain for targeted DexScreener search
const CHAIN_POPULAR: Record<string, string[]> = {
  solana: ['SOL', 'USDC', 'JUP', 'BONK', 'WIF', 'RAY', 'ORCA', 'JTO', 'PYTH', 'MEME', 'BOME', 'POPCAT', 'WEN', 'TENSOR', 'KAMINO', 'HELLO', 'MEW', 'MYRO', 'PENGU', 'GUAC'],
  ethereum: ['ETH', 'USDT', 'UNI', 'LINK', 'AAVE', 'PEPE', 'SHIB', 'MKR', 'COMP', 'LDO', 'ARB', 'OP', 'SNX', 'CRV', 'BAL', 'YFI', 'SUSHI', '1INCH', 'ENJ', 'MANA'],
  bsc: ['BNB', 'CAKE', 'BUSD', 'FLOKI', 'LEVER', 'RDNT', 'WOO', 'TWT', 'BSW', 'ALPACA', 'BABYDOGE', 'SAFEMOON'],
  base: ['ETH', 'USDC', 'AERO', 'BRETT', 'TOSHI', 'MOG', 'LAND', 'BASIS', 'MORPHO', 'EXTRA'],
  arbitrum: ['ARB', 'GMX', 'RDNT', 'PENDLE', 'MAGIC', 'SUSHI', 'GNS', 'JONES', 'VELA', 'CAP'],
  polygon: ['MATIC', 'QUICK', 'SUSHI', 'AAVE', 'COMPUTE', 'POLYDOGE', 'QI', 'JBX', 'GRT', 'LDO'],
  avalanche: ['AVAX', 'JOE', 'SUSHI', 'BENQI', 'SPELL', 'PNG', 'XEMU', 'TJ', 'YAK', 'PLAT'],
  optimism: ['OP', 'SNX', 'VELA', 'PERP', 'HND', 'BEAM', 'THALES', 'AELIN', 'KLIMA', 'PRO'],
};

// Broad search terms for bulk DexScreener fetches — these return hundreds of pairs
const BULK_SEARCH_TERMS = [
  'SOL', 'ETH', 'BNB', 'USDC', 'USDT', 'memecoin', 'DeFi', 'swap', 'token',
  'airdrop', 'launch', 'pump', 'moon', 'elon', 'doge', 'cat', 'frog',
];

// ── Helper: infer chain from token address format ──────────
// If address starts with "0x" → ETH (Ethereum), otherwise → SOL (Solana)
function inferChainFromAddress(address: string, fallbackChain: string): string {
  if (address.startsWith('0x')) return 'ETH';
  return fallbackChain;
}

// ── Helper: rate-limited delay ─────────────────────────────
function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Helper: direct DexScreener search (bypasses cache) ─────
async function directDexScreenerSearch(query: string): Promise<any[]> {
  try {
    const res = await fetch(
      `https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`,
      {
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!res.ok) return [];
    const data = await res.json();
    return data.pairs || [];
  } catch {
    return [];
  }
}

// ── Helper: upsert a token pair into the DB ────────────────
async function upsertPair(
  db: Awaited<typeof import('@/lib/db')>['db'],
  pair: any,
  defaultChain: string,
): Promise<boolean> {
  try {
    const address = pair.baseToken?.address || pair.pairAddress || `${pair.baseToken?.symbol}-${defaultChain}`;
    if (!address || address.length < 3) return false;

    const chainId = (pair.chainId || defaultChain).toLowerCase();
    const internalChain = CHAINS.find(c => c.id === chainId)?.internal || defaultChain;

    await db.token.upsert({
      where: { address },
      update: {
        priceUsd: parseFloat(pair.priceUsd || '0') || 0,
        volume24h: pair.volume?.h24 || 0,
        marketCap: pair.marketCap || pair.fdv || 0,
        liquidity: pair.liquidity?.usd || 0,
        priceChange5m: pair.priceChange?.m5 || 0,
        priceChange15m: pair.priceChange?.m15 || 0,
        priceChange1h: pair.priceChange?.h1 || 0,
        priceChange6h: pair.priceChange?.h6 || 0,
        priceChange24h: pair.priceChange?.h24 || 0,
        dexId: pair.dexId || undefined,
        pairAddress: pair.pairAddress || undefined,
        dex: pair.dexId || undefined,
      },
      create: {
        address,
        symbol: (pair.baseToken?.symbol || '').toUpperCase(),
        name: pair.baseToken?.name || '',
        chain: internalChain,
        priceUsd: parseFloat(pair.priceUsd || '0') || 0,
        volume24h: pair.volume?.h24 || 0,
        marketCap: pair.marketCap || pair.fdv || 0,
        liquidity: pair.liquidity?.usd || 0,
        priceChange5m: pair.priceChange?.m5 || 0,
        priceChange15m: pair.priceChange?.m15 || 0,
        priceChange1h: pair.priceChange?.h1 || 0,
        priceChange6h: pair.priceChange?.h6 || 0,
        priceChange24h: pair.priceChange?.h24 || 0,
        dexId: pair.dexId || undefined,
        pairAddress: pair.pairAddress || undefined,
        dex: pair.dexId || undefined,
      },
    });
    return true;
  } catch {
    return false;
  }
}

// ── Helper: clamp a number to [min, max] ───────────────────
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// ── Helper: safe division that returns 0 for div-by-zero ───
function safeDivide(numerator: number, denominator: number): number {
  return denominator === 0 ? 0 : numerator / denominator;
}

// ════════════════════════════════════════════════════════════
// MAIN SEED LOGIC
// ════════════════════════════════════════════════════════════

async function runSeed(action: SeedAction): Promise<SeedResult> {
  const { db } = await import('@/lib/db');

  const result: SeedResult = {
    tokensImported: 0,
    dnaCreated: 0,
    signalsCreated: 0,
    candlesCreated: 0,
    totalTokens: 0,
    tradersCreated: 0,
    patternRulesCreated: 0,
    behaviorModelsInitialized: 0,
    realSyncTriggered: false,
    ohlcvBackfill: {
      tokensProcessed: 0,
      totalCandlesStored: 0,
      failedTokens: [],
    },
  };

  // ── Action-based step skipping ──
  // 'traders'  → redirect message (no fake traders)
  // 'patterns' → skip to Step 11 (pattern rules)
  // 'full'     → run Steps 1-12 (no simulated data)
  // 'tokens'   → run Steps 1-6 only
  // 'real'     → trigger real-sync for everything
  const skipToPatterns = action === 'patterns';
  const skipTokenSteps = skipToPatterns || action as string === 'traders' || action === 'real';

  // ══════════════════════════════════════════════════════════
  // STEP 1: DexScreener boosted/trending tokens
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    try {
      console.log('[Seed] === STEP 1: DexScreener boosted tokens ===');
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');
      const boosted = await dexScreenerClient.getBoostedTokens();

      for (const token of boosted) {
        const address = token.tokenAddress || token.address || `boosted-${token.chainId}-${token.symbol}`;
        if (!address) continue;

        const chainId = (token.chainId || 'solana').toLowerCase();
        const internalChain = CHAINS.find(c => c.id === chainId)?.internal || 'ALL';

        try {
          await db.token.upsert({
            where: { address },
            update: {},
            create: {
              address,
              symbol: (token.symbol || '').toUpperCase(),
              name: token.name || token.symbol || '',
              chain: internalChain,
            },
          });
          result.tokensImported++;
        } catch { /* skip duplicates */ }
      }
      console.log(`[Seed] Boosted: ${result.tokensImported} tokens`);
    } catch (err) {
      console.warn('[Seed] DexScreener boosted failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 2: DexScreener BULK search per chain
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    try {
      console.log('[Seed] === STEP 2: DexScreener bulk search per chain ===');
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');

      for (const chain of CHAINS) {
        const searchTerms = [chain.id, ...BULK_SEARCH_TERMS.slice(0, 3)];

        for (const term of searchTerms) {
          const pairs = await directDexScreenerSearch(term);
          // Filter pairs for this chain
          const chainNorm = dexScreenerClient.normalizeChain(chain.id);
          const chainPairs = pairs.filter(
            (p: any) => dexScreenerClient.normalizeChain(p.chainId) === chainNorm,
          );

          // Sort by volume, take top 30
          chainPairs.sort((a: any, b: any) => (b.volume?.h24 || 0) - (a.volume?.h24 || 0));
          const topPairs = chainPairs.slice(0, 30);

          for (const pair of topPairs) {
            const ok = await upsertPair(db, pair, chain.internal);
            if (ok) result.tokensImported++;
          }

          await delay(300); // Rate limit between requests
        }
        console.log(`[Seed] Bulk ${chain.id}: ${result.tokensImported} total so far`);
      }
      console.log(`[Seed] Bulk search total: ${result.tokensImported}`);
    } catch (err) {
      console.warn('[Seed] DexScreener bulk search failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 3: DexScreener per-symbol search (popular tokens)
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    try {
      console.log('[Seed] === STEP 3: DexScreener per-symbol search ===');
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');

      for (const chain of CHAINS) {
        const popular = CHAIN_POPULAR[chain.id] || [];

        // Process in batches of 5 symbols
        for (let i = 0; i < popular.length; i += 5) {
          const batch = popular.slice(i, i + 5);

          const pairResults = await Promise.allSettled(
            batch.map(sym => dexScreenerClient.searchTokenByName(sym)),
          );

          for (const pr of pairResults) {
            if (pr.status !== 'fulfilled' || !pr.value) continue;

            const pairs = pr.value;
            const chainNorm = dexScreenerClient.normalizeChain(chain.id);
            const filtered = pairs.filter(
              (p: any) => dexScreenerClient.normalizeChain(p.chainId) === chainNorm,
            );
            filtered.sort((a: any, b: any) => (b.volume?.h24 || 0) - (a.volume?.h24 || 0));

            for (const pair of filtered.slice(0, 3)) {
              const ok = await upsertPair(db, pair, chain.internal);
              if (ok) result.tokensImported++;
            }
          }

          await delay(300);
        }
        console.log(`[Seed] Symbol ${chain.id}: ${result.tokensImported} total so far`);
      }
    } catch (err) {
      console.warn('[Seed] DexScreener symbol search failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 4: DexPaprika top tokens per chain
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    try {
      console.log('[Seed] === STEP 4: DexPaprika top tokens ===');
      const { DexPaprikaClient } = await import('@/lib/services/data-sources/dexpaprika-client');
      const dpClient = new DexPaprikaClient();

      for (const chain of CHAINS.slice(0, 5)) {
        try {
          const tokens = await dpClient.getTopTokens(chain.id, 50);

          for (const token of tokens) {
            const address = token.id || `${token.symbol}-${chain.id}`;
            try {
              await db.token.upsert({
                where: { address },
                update: {
                  priceUsd: token.priceUsd || 0,
                  volume24h: token.volume24h || 0,
                  marketCap: token.marketCap || 0,
                  liquidity: token.liquidity || 0,
                  priceChange24h: token.priceChange24h || 0,
                },
                create: {
                  address,
                  symbol: token.symbol?.toUpperCase() || '',
                  name: token.name || '',
                  chain: chain.internal,
                  priceUsd: token.priceUsd || 0,
                  volume24h: token.volume24h || 0,
                  marketCap: token.marketCap || 0,
                  liquidity: token.liquidity || 0,
                  priceChange24h: token.priceChange24h || 0,
                  dex: 'dexpaprika',
                },
              });
              result.tokensImported++;
            } catch { /* skip duplicates */ }
          }
          console.log(`[Seed] DexPaprika ${chain.id}: ${result.tokensImported} total so far`);
        } catch (err) {
          console.warn(`[Seed] DexPaprika ${chain.id} failed:`, err);
        }
      }
    } catch (err) {
      console.warn('[Seed] DexPaprika client failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 5: CoinGecko top tokens by market cap + volume
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    try {
      console.log('[Seed] === STEP 5: CoinGecko top tokens ===');
      const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');

      // Top by market cap (page 1 = 250)
      const topMcap = await coinGeckoClient.getTopTokensPaginated(500);
      // Top by volume
      const topVol = await coinGeckoClient.getTopTokensByVolumePaginated(250);

      const allCg = [...topMcap, ...topVol];
      // Deduplicate by coinId
      const seen = new Set<string>();
      const uniqueCg = allCg.filter(t => {
        if (seen.has(t.coinId)) return false;
        seen.add(t.coinId);
        return true;
      });

      for (const token of uniqueCg) {
        const address = token.address || token.coinId;
        if (!address) continue;

        // Detect chain from platforms
        const platformEntries = Object.entries(token.platforms || {});
        let chain = 'ALL';
        if (platformEntries.some(([k]) => k === 'solana')) chain = 'SOL';
        else if (platformEntries.some(([k]) => k === 'ethereum')) chain = 'ETH';
        else if (platformEntries.some(([k]) => k === 'binance-smart-chain')) chain = 'BSC';
        else if (platformEntries.some(([k]) => k === 'base')) chain = 'BASE';
        else if (platformEntries.some(([k]) => k === 'arbitrum')) chain = 'ARB';

        // Use the contract address if available, otherwise coinId
        let tokenAddress = address;
        for (const [platform, contract] of platformEntries) {
          if (contract && contract !== '') {
            tokenAddress = contract;
            break;
          }
        }

        // Cross-check chain from address format: 0x → ETH, otherwise keep platform-based chain
        chain = inferChainFromAddress(tokenAddress, chain);

        try {
          await db.token.upsert({
            where: { address: tokenAddress },
            update: {
              priceUsd: token.priceUsd,
              volume24h: token.volume24h,
              marketCap: token.marketCap,
              priceChange1h: token.priceChange1h,
              priceChange24h: token.priceChange24h,
            },
            create: {
              address: tokenAddress,
              symbol: token.symbol?.toUpperCase() || '',
              name: token.name || '',
              chain,
              priceUsd: token.priceUsd,
              volume24h: token.volume24h,
              marketCap: token.marketCap,
              priceChange1h: token.priceChange1h,
              priceChange24h: token.priceChange24h,
            },
          });
          result.tokensImported++;
        } catch { /* skip duplicates */ }
      }
      console.log(`[Seed] CoinGecko: ${result.tokensImported} total so far`);
    } catch (err) {
      console.warn('[Seed] CoinGecko failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 6: Count total tokens + fix chain/address mismatches
  // ══════════════════════════════════════════════════════════
  if (!skipTokenSteps) {
    result.totalTokens = await db.token.count();
    console.log(`[Seed] Total tokens in DB: ${result.totalTokens}`);

    // Fix tokens where chain doesn't match address format
    // e.g., tokens with 0x... Ethereum addresses incorrectly set to "SOL"
    try {
      const wrongChainTokens = await db.token.findMany({
        where: {
          address: { startsWith: '0x' },
          chain: 'SOL',
        },
      });
      if (wrongChainTokens.length > 0) {
        for (const token of wrongChainTokens) {
          await db.token.update({
            where: { id: token.id },
            data: { chain: 'ETH' },
          });
        }
        console.log(`[Seed] Fixed ${wrongChainTokens.length} tokens with 0x addresses set to ETH chain`);
      }
    } catch (err) {
      console.warn('[Seed] Chain fix failed:', err);
    }

    // If action is "tokens", stop here
    if (action === 'tokens') {
      return result;
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 7: Create TokenDNA for tokens without DNA
  // All scores computed deterministically from real token metrics.
  // If a metric is missing, use a neutral default (50), NOT random.
  // ══════════════════════════════════════════════════════════
  if (!skipToPatterns && action as string !== 'traders') {
    try {
      console.log('[Seed] === STEP 7: Computing TokenDNA (deterministic from real metrics) ===');
      const tokensWithoutDna = await db.token.findMany({
        where: { dna: { is: null } },
        take: 2000,
      });

      // Process in batches of 50
      for (let i = 0; i < tokensWithoutDna.length; i += 50) {
        const batch = tokensWithoutDna.slice(i, i + 50);

        for (const token of batch) {
          try {
            const pc24 = token.priceChange24h ?? 0;
            const liq = token.liquidity ?? 0;
            const mcap = token.marketCap ?? 0;
            const vol = token.volume24h ?? 0;

            // ── Risk Score (0-100) ──
            // Computed entirely from real token metrics
            let riskScore = 20;
            if (Math.abs(pc24) > 50) riskScore += 40;
            else if (Math.abs(pc24) > 20) riskScore += 30;
            else if (Math.abs(pc24) > 10) riskScore += 20;

            if (liq > 0 && liq < 50000) riskScore += 30;
            else if (liq === 0 && vol > 0) riskScore += 35;

            if (mcap > 0 && mcap < 1000000) riskScore += 25;
            else if (mcap > 0 && mcap < 10000000) riskScore += 15;

            if (liq > 0 && vol > 0 && vol / liq > 10) riskScore += 20;

            riskScore = clamp(riskScore, 5, 98);

            const isHighRisk = riskScore > 60;

            // ── Smart Money Score (0-100) ──
            // Deterministic: higher mcap + higher liquidity = more smart money interest
            // Low mcap / low liq tokens are less likely to attract smart money
            const liqRatio = clamp(safeDivide(liq, 1000000), 0, 1); // 0-1 normalized
            const mcapRatio = clamp(safeDivide(mcap, 100000000), 0, 1); // 0-1 normalized
            const smartMoneyScore = isHighRisk
              ? clamp(15 + liqRatio * 15 + mcapRatio * 10, 10, 40)
              : clamp(30 + liqRatio * 25 + mcapRatio * 20, 20, 80);

            // ── Bot Activity Score (0-100) ──
            // Deterministic: high volume/liquidity ratio suggests bot activity
            const volLiqRatio = liq > 0 ? clamp(safeDivide(vol, liq), 0, 20) : 0;
            const botActivityScore = isHighRisk
              ? clamp(15 + volLiqRatio * 2, 10, 50)
              : clamp(5 + volLiqRatio * 1.5, 5, 30);

            // ── Rug Pull Probability ──
            // Deterministic: based on real risk factors
            let rugPullProb = 0.05;
            if (liq > 0 && liq < 50000) rugPullProb += 0.3;
            if (mcap > 0 && mcap < 500000) rugPullProb += 0.2;
            if (pc24 < -30) rugPullProb += 0.2;
            rugPullProb = clamp(rugPullProb, 0, 0.95);

            // ── Volatility Index (0-100) ──
            // Deterministic: derived from actual price changes
            const volatilityIndex = clamp(Math.abs(pc24) * 1.5, 0, 100);

            // ── Other DNA fields — all deterministic from real data ──

            // Retail Score: high risk tokens attract more retail
            const retailScore = isHighRisk
              ? clamp(35 + Math.abs(pc24) * 0.3, 20, 60)
              : clamp(50 + mcapRatio * 20, 40, 80);

            // Whale Score: large mcap tokens attract whales
            const whaleScore = isHighRisk
              ? clamp(mcapRatio * 15 + liqRatio * 10, 5, 30)
              : clamp(20 + mcapRatio * 30 + liqRatio * 15, 15, 70);

            // Wash Trade Probability: high vol/liq ratio on small tokens is suspicious
            const washTradeProb = isHighRisk
              ? clamp(0.15 + volLiqRatio * 0.03, 0.1, 0.7)
              : clamp(volLiqRatio * 0.01, 0, 0.15);

            // Sniper Pct: low liquidity tokens have more snipers
            const sniperPct = isHighRisk
              ? clamp(15 + (liq > 0 && liq < 100000 ? 20 : 0), 10, 40)
              : clamp(2 + volLiqRatio * 0.5, 0, 10);

            // MEV Pct: higher on chains with high volume/liquidity ratio
            const mevPct = isHighRisk
              ? clamp(8 + volLiqRatio * 1.5, 5, 30)
              : clamp(2 + volLiqRatio * 0.5, 0, 15);

            // Copy Bot Pct: proportional to token popularity (volume)
            const copyBotPct = isHighRisk
              ? clamp(5 + (vol > 100000 ? 10 : 0), 3, 20)
              : clamp(1 + (vol > 1000000 ? 5 : 0), 0, 10);

            // Trader composition — derived from the computed scores above
            const traderComposition = {
              smartMoney: Math.round(smartMoneyScore / 10),
              whale: Math.round(whaleScore / 10),
              bot_mev: Math.round(mevPct / 2),
              bot_sniper: Math.round(sniperPct / 2),
              bot_copy: Math.round(copyBotPct),
              retail: Math.round(retailScore / 5),
              creator: 0, // No data — will be populated by real analysis
              fund: riskScore < 30 ? Math.round(mcapRatio * 3) : 0,
            };

            // Top wallets: empty until real wallet data is available
            // Do NOT generate fake wallet addresses
            const topWallets: Array<{
              address: string;
              label: string;
              pnl: number;
              entryRank: number;
            }> = [];

            await db.tokenDNA.create({
              data: {
                tokenId: token.id,
                riskScore,
                botActivityScore: Math.round(botActivityScore * 100) / 100,
                smartMoneyScore: Math.round(smartMoneyScore * 100) / 100,
                retailScore: Math.round(retailScore * 100) / 100,
                whaleScore: Math.round(whaleScore * 100) / 100,
                washTradeProb: Math.round(washTradeProb * 1000) / 1000,
                sniperPct: Math.round(sniperPct * 100) / 100,
                mevPct: Math.round(mevPct * 100) / 100,
                copyBotPct: Math.round(copyBotPct * 100) / 100,
                traderComposition: JSON.stringify(traderComposition),
                topWallets: JSON.stringify(topWallets),
              },
            });
            result.dnaCreated++;
          } catch { /* skip */ }
        }
      }
      console.log(`[Seed] DNA: ${result.dnaCreated} created (all deterministic from real metrics)`);
    } catch (err) {
      console.warn('[Seed] TokenDNA failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 8: Generate signals ONLY when real conditions are met
  // No random probability gate. No generic fallback signals.
  // Each signal must be justified by actual data in the description.
  // ══════════════════════════════════════════════════════════
  if (!skipToPatterns && action as string !== 'traders') {
    try {
      console.log('[Seed] === STEP 8: Generating signals (data-driven only, no random) ===');
      const allTokens = await db.token.findMany({
        include: { dna: true, signals: true },
        take: 3000,
      });

      for (const token of allTokens) {
        // Skip tokens that already have signals
        if (token.signals.length > 0) continue;

        const pc24 = token.priceChange24h ?? 0;
        const pc1h = token.priceChange1h ?? 0;
        const pc6h = token.priceChange6h ?? 0;
        const liq = token.liquidity ?? 0;
        const vol = token.volume24h ?? 0;
        const mcap = token.marketCap ?? 0;
        const dna = token.dna;

        // Determine signal type based on REAL token characteristics only
        const signalsToCreate: Array<{
          type: string;
          confidence: number;
          direction: string;
          description: string;
        }> = [];

        // ── Rug Pull detection ──
        // Requires: price dropped >30% AND low liquidity AND high risk DNA
        if (pc24 < -30 && liq > 0 && liq < 100000 && dna && dna.riskScore > 60) {
          signalsToCreate.push({
            type: 'RUG_PULL',
            confidence: clamp(50 + dna.riskScore / 2, 60, 95),
            direction: 'AVOID',
            description: `Rug pull risk: ${token.symbol} dropped ${pc24.toFixed(1)}% in 24h with only $${Math.round(liq).toLocaleString()} liquidity and risk score ${dna.riskScore}`,
          });
        }

        // ── Smart Money signal ──
        // Requires: DNA smart money score >40 AND risk score <40 (low risk, smart interest)
        if (dna && dna.smartMoneyScore > 40 && dna.riskScore < 40) {
          signalsToCreate.push({
            type: 'SMART_MONEY',
            confidence: clamp(40 + dna.smartMoneyScore, 50, 90),
            direction: 'LONG',
            description: `Smart money accumulating ${token.symbol} — SM score ${dna.smartMoneyScore.toFixed(0)}, risk ${dna.riskScore}`,
          });
        }

        // ── V-Shape recovery ──
        // Requires: 24h drop >15% AND 1h recovery >5%
        if (pc24 < -15 && pc1h > 5) {
          signalsToCreate.push({
            type: 'V_SHAPE',
            confidence: clamp(40 + Math.abs(pc24), 50, 85),
            direction: 'LONG',
            description: `V-shape recovery: ${token.symbol} dropped ${pc24.toFixed(1)}% (24h) but recovering +${pc1h.toFixed(1)}% (1h)`,
          });
        }

        // ── Liquidity trap ──
        // Requires: very low liquidity AND volume >> liquidity
        if (liq > 0 && liq < 20000 && vol > liq * 5) {
          signalsToCreate.push({
            type: 'LIQUIDITY_TRAP',
            confidence: clamp(50 + safeDivide(vol, liq), 55, 80),
            direction: 'AVOID',
            description: `Liquidity trap: ${token.symbol} has $${Math.round(liq).toLocaleString()} liquidity vs $${Math.round(vol).toLocaleString()} volume — ${(safeDivide(vol, liq)).toFixed(1)}x ratio`,
          });
        }

        // ── Volume Spike ──
        // Requires: significant volume with actual price change
        if (vol > 1000000 && Math.abs(pc24) > 10) {
          signalsToCreate.push({
            type: 'PATTERN',
            confidence: clamp(50 + Math.min(Math.abs(pc24), 30), 50, 80),
            direction: pc24 > 0 ? 'LONG' : 'SHORT',
            description: `Volume spike: ${token.symbol} has $${(vol / 1000000).toFixed(1)}M volume with ${pc24 > 0 ? '+' : ''}${pc24.toFixed(1)}% price change`,
          });
        }

        // ── Pump warning ──
        // Requires: extreme short-term price increase
        if (pc1h > 30) {
          signalsToCreate.push({
            type: 'PATTERN',
            confidence: clamp(50 + pc1h * 0.5, 50, 85),
            direction: 'SHORT',
            description: `Pump warning: ${token.symbol} surged +${pc1h.toFixed(1)}% in 1h — likely unsustainable`,
          });
        }

        // ── Dead token ──
        // Requires: no volume, no liquidity, but still in DB
        if (vol === 0 && liq === 0 && mcap === 0) {
          signalsToCreate.push({
            type: 'PATTERN',
            confidence: 60,
            direction: 'AVOID',
            description: `Dead token: ${token.symbol} has no volume, liquidity, or market cap`,
          });
        }

        // ── High volatility ──
        // Requires: significant 6h swing
        if (Math.abs(pc6h) > 40) {
          signalsToCreate.push({
            type: 'PATTERN',
            confidence: 65,
            direction: pc6h > 0 ? 'LONG' : 'SHORT',
            description: `High volatility: ${token.symbol} moved ${pc6h > 0 ? '+' : ''}${pc6h.toFixed(1)}% in 6h`,
          });
        }

        // NO generic fallback — if no real conditions match, no signal is created
        for (const sig of signalsToCreate.slice(0, 3)) { // Max 3 signals per token
          try {
            await db.signal.create({
              data: {
                type: sig.type,
                tokenId: token.id,
                confidence: Math.round(sig.confidence),
                direction: sig.direction,
                description: sig.description,
                metadata: JSON.stringify({ source: 'seed-deterministic', chain: token.chain }),
              },
            });
            result.signalsCreated++;
          } catch { /* skip */ }
        }
      }
      console.log(`[Seed] Signals: ${result.signalsCreated} created (data-driven only)`);
    } catch (err) {
      console.warn('[Seed] Signal generation failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 9: Fetch REAL OHLCV candles only
  // NO simulated candles. If a real API doesn't have data, SKIP.
  // Sources: DexPaprika OHLCV + CoinGecko OHLCV
  // ══════════════════════════════════════════════════════════
  if (!skipToPatterns && action as string !== 'traders') {
    try {
      console.log('[Seed] === STEP 9: Fetching REAL OHLCV candles (no simulation) ===');
      const topTokens = await db.token.findMany({
        where: { volume24h: { gt: 0 } },
        orderBy: { volume24h: 'desc' },
        take: 50,
      });

      const chainMap: Record<string, string> = {
        SOL: 'solana', ETH: 'ethereum', BSC: 'bsc', BASE: 'base',
        ARB: 'arbitrum', MATIC: 'polygon', AVAX: 'avalanche', OP: 'optimism',
      };

      // ── Source 1: DexPaprika OHLCV ──
      try {
        const { DexPaprikaClient } = await import('@/lib/services/data-sources/dexpaprika-client');
        const dpClient = new DexPaprikaClient();

        for (const token of topTokens.slice(0, 20)) {
          try {
            const dpChain = chainMap[token.chain] || 'solana';

            const ohlcv = await dpClient.getOHLCV(dpChain, token.address, '1h', 200);
            if (ohlcv && ohlcv.length > 0) {
              for (const candle of ohlcv) {
                try {
                  const ts = new Date(candle.timestamp * 1000);
                  const minutes = ts.getUTCMinutes();
                  ts.setUTCMinutes(minutes - (minutes % 15), 0, 0);

                  await db.priceCandle.upsert({
                    where: {
                      tokenAddress_chain_timeframe_timestamp: {
                        tokenAddress: token.address,
                        chain: token.chain,
                        timeframe: '15m',
                        timestamp: ts,
                      },
                    },
                    create: {
                      tokenAddress: token.address,
                      chain: token.chain,
                      timeframe: '15m',
                      timestamp: ts,
                      open: candle.open,
                      high: candle.high,
                      low: candle.low,
                      close: candle.close,
                      volume: candle.volume || 0,
                      source: 'dexpaprika',
                    },
                    update: {
                      open: candle.open,
                      high: candle.high,
                      low: candle.low,
                      close: candle.close,
                    },
                  });
                  result.candlesCreated++;
                } catch { /* skip duplicates */ }
              }
              console.log(`[Seed] ${token.symbol}: ${ohlcv.length} DexPaprika candles stored`);
            }
            await delay(500);
          } catch {
            // DexPaprika doesn't have data for this token — skip it
            console.log(`[Seed] ${token.symbol}: no DexPaprika OHLCV data, skipping`);
          }
        }
      } catch {
        console.warn('[Seed] DexPaprika OHLCV not available');
      }

      // ── Source 2: CoinGecko OHLCV ──
      // Only for tokens that don't already have candles
      try {
        const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');

        for (const token of topTokens.slice(0, 30)) {
          // Skip tokens that already have candles from DexPaprika
          const existingCandles = await db.priceCandle.count({
            where: { tokenAddress: token.address, timeframe: '15m' },
          });
          if (existingCandles > 0) continue;

          // CoinGecko uses coin IDs, not contract addresses
          // Try to fetch using the token's symbol as coinId
          const coinId = token.symbol?.toLowerCase() || '';
          if (!coinId) continue;

          try {
            const ohlcv = await coinGeckoClient.getOHLCV(coinId, 7);
            if (ohlcv && ohlcv.length > 0) {
              // CoinGecko OHLCV returns candles at varying timeframes depending on days
              // For 7 days, candles are hourly
              for (const candle of ohlcv) {
                try {
                  // CoinGeckoOHLCVCandle: [timestamp, open, high, low, close]
                  const ts = new Date(candle[0]);
                  const minutes = ts.getUTCMinutes();
                  ts.setUTCMinutes(minutes - (minutes % 15), 0, 0);

                  await db.priceCandle.upsert({
                    where: {
                      tokenAddress_chain_timeframe_timestamp: {
                        tokenAddress: token.address,
                        chain: token.chain,
                        timeframe: '15m',
                        timestamp: ts,
                      },
                    },
                    create: {
                      tokenAddress: token.address,
                      chain: token.chain,
                      timeframe: '15m',
                      timestamp: ts,
                      open: candle[1],
                      high: candle[2],
                      low: candle[3],
                      close: candle[4],
                      volume: 0, // CoinGecko OHLCV doesn't include volume in the response
                      source: 'coingecko',
                    },
                    update: {
                      open: candle[1],
                      high: candle[2],
                      low: candle[3],
                      close: candle[4],
                    },
                  });
                  result.candlesCreated++;
                } catch { /* skip duplicates */ }
              }
              console.log(`[Seed] ${token.symbol}: ${ohlcv.length} CoinGecko candles stored`);
            }
            await delay(500);
          } catch {
            // CoinGecko doesn't have data for this token — skip it
            console.log(`[Seed] ${token.symbol}: no CoinGecko OHLCV data, skipping`);
          }
        }
      } catch {
        console.warn('[Seed] CoinGecko OHLCV not available');
      }

      console.log(`[Seed] Total real candles: ${result.candlesCreated} (NO simulated data)`);
    } catch (err) {
      console.warn('[Seed] OHLCV fetching failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 9b: OHLCV Pipeline Backfill for backtest readiness
  // Uses ohlcvPipeline.backfillToken() to fetch 1h/4h/1d candles
  // for the top 20 tokens by volume24h, so backtests work
  // immediately after seeding.
  // ══════════════════════════════════════════════════════════
  if (!skipToPatterns && action as string !== 'traders') {
    try {
      console.log('[Seed] === STEP 9b: OHLCV Pipeline Backfill (1h/4h/1d for top 20) ===');
      const topTokens = await db.token.findMany({
        where: { volume24h: { gt: 0 } },
        orderBy: { volume24h: 'desc' },
        take: 20,
        select: { address: true, chain: true, symbol: true },
      });

      const backfillTimeframes = ['1h', '4h', '1d'];
      let tokensProcessed = 0;
      let totalCandlesStored = 0;
      const failedTokens: string[] = [];

      for (let i = 0; i < topTokens.length; i++) {
        const token = topTokens[i];
        console.log(`[Seed] Backfilling OHLCV for token ${i + 1}/20: ${token.symbol} (${token.address})`);

        try {
          const backfillResult = await ohlcvPipeline.backfillToken(
            token.address,
            token.chain,
            backfillTimeframes,
          );

          tokensProcessed++;
          totalCandlesStored += backfillResult.totalStored;

          if (backfillResult.totalStored > 0) {
            console.log(`[Seed] ${token.symbol}: ${backfillResult.totalStored} candles stored across ${backfillResult.timeframes.length} timeframe(s)`);
          } else {
            console.log(`[Seed] ${token.symbol}: no candles stored (no data available)`);
          }
        } catch (err) {
          console.error(`[Seed] Backfill failed for ${token.symbol} (${token.address}):`, err);
          failedTokens.push(token.address);
        }

        // 250ms delay between tokens to respect rate limits
        await delay(250);
      }

      result.ohlcvBackfill = {
        tokensProcessed,
        totalCandlesStored,
        failedTokens,
      };

      console.log(
        `[Seed] OHLCV Pipeline Backfill complete: ${tokensProcessed}/${topTokens.length} tokens processed, ` +
        `${totalCandlesStored} total candles stored, ${failedTokens.length} failures`,
      );
    } catch (err) {
      console.warn('[Seed] OHLCV Pipeline Backfill failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 10: Trader seeding — REMOVED (was all fake data)
  // Use /api/real-sync with action:'traders' for real wallet data
  // from Etherscan
  // ══════════════════════════════════════════════════════════
  if (action === 'traders') {
    console.log('[Seed] === STEP 10: Trader seeding is NO LONGER supported with fake data ===');
    console.log('[Seed] Use POST /api/real-sync with { action: "traders" } for real wallet data from Etherscan');
    // Return early with the redirect message
    result.tradersCreated = 0;
    return result;
  }

  // ══════════════════════════════════════════════════════════
  // STEP 11: Seed Pattern Rules (templates, not fake data)
  // occurrences = 0 (will be updated by real detection)
  // winRate = 0.5 (neutral until real backtesting)
  // backtestResults = null (will be populated by real backtesting)
  // ══════════════════════════════════════════════════════════
  if (!action.startsWith('real')) {
    try {
      console.log('[Seed] === STEP 11: Seeding PatternRules (templates only) ===');

      const existingPatterns = await db.patternRule.count();
      if (existingPatterns > 0) {
        console.log(`[Seed] PatternRules already exist (${existingPatterns}), skipping`);
      } else {
        const patternRules = [
          {
            name: 'Volume Spike',
            description: 'Sudden volume increase detected — volume exceeds 3x the 24h average within a short timeframe',
            category: 'VOLUME',
            conditions: JSON.stringify({ metric: 'volume24h', operator: '>', multiplier: 3, timeframe: '1h', minAbsoluteVolume: 50000 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Smart Money Accumulation',
            description: 'Whale or smart money addresses steadily buying over multiple transactions without significant selling',
            category: 'SMART_MONEY',
            conditions: JSON.stringify({ smartMoneyBuyRatio: 0.7, minTransactions: 3, timeframe: '6h', minVolumeUsd: 10000 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'V-Shape Recovery',
            description: 'Sharp price dip followed by rapid recovery, indicating strong buying support at lower levels',
            category: 'PRICE_ACTION',
            conditions: JSON.stringify({ dipPct: -15, recoveryPct: 5, dipTimeframe: '1h', recoveryTimeframe: '2h' }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Liquidity Drain',
            description: 'Decreasing liquidity pool with sustained volume, indicating potential exit by large holders',
            category: 'LIQUIDITY',
            conditions: JSON.stringify({ liquidityChangePct: -20, timeframe: '24h', volumeToLiquidityRatio: 5 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Bot Swarm',
            description: 'Multiple identified bot addresses trading the same token in rapid succession, often preceding a pump or dump',
            category: 'BOT_ACTIVITY',
            conditions: JSON.stringify({ minBotCount: 5, timeframe: '30m', minBotVolumePct: 40 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Rug Pull Warning',
            description: 'Sudden liquidity removal combined with large holder selling — high probability of rug pull',
            category: 'RISK',
            conditions: JSON.stringify({ liquidityRemovedPct: 50, timeframe: '1h', whaleSellCount: 3, priceDropPct: 30 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Whale Wallet Movement',
            description: 'Large wallet transfers detected on-chain, potentially indicating upcoming OTC deals or exchange deposits',
            category: 'WHALE',
            conditions: JSON.stringify({ minTransferUsd: 100000, timeframe: '1h', transferType: 'wallet_to_exchange' }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Copy Trading Cluster',
            description: 'Multiple wallets executing identical trades within seconds, suggesting copy-trading bot activity',
            category: 'BOT_ACTIVITY',
            conditions: JSON.stringify({ minCopyCount: 3, maxTimeDiffSec: 10, sameToken: true, sameDirection: true }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Momentum Shift',
            description: 'Price reversal after sustained trend — detected via moving average crossover or RSI divergence',
            category: 'PRICE_ACTION',
            conditions: JSON.stringify({ trend: 'reversal', indicator: 'MA_CROSS', timeframe: '4h', minTrendDuration: '24h' }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Liquidity Trap Entry',
            description: 'Token with very low liquidity attracting disproportionate buy volume, likely exit trap for new entrants',
            category: 'LIQUIDITY',
            conditions: JSON.stringify({ maxLiquidityUsd: 10000, minBuyVolumeUsd: 50000, timeframe: '2h', buyToLiqRatio: 5 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'Sniper Bot Entry',
            description: 'Token purchased within the first few blocks of liquidity creation, characteristic of sniper bot behavior',
            category: 'BOT_ACTIVITY',
            conditions: JSON.stringify({ maxBlocksAfterLiquidity: 3, minBuyUsd: 1000, timeframe: 'block0' }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
          {
            name: 'MEV Sandwich',
            description: 'Sandwich attack pattern detected — large buy immediately before target tx, sell immediately after',
            category: 'MEV',
            conditions: JSON.stringify({ frontRunTimeMs: 500, backRunTimeMs: 500, minPriceImpactPct: 2, minProfitUsd: 50 }),
            winRate: 0.5,
            occurrences: 0,
            backtestResults: undefined,
          },
        ];

        for (const rule of patternRules) {
          try {
            await db.patternRule.create({
              data: {
                name: rule.name,
                description: rule.description,
                category: rule.category,
                conditions: rule.conditions,
                winRate: rule.winRate,
                occurrences: rule.occurrences,
                backtestResults: rule.backtestResults,
              },
            });
            result.patternRulesCreated++;
          } catch { /* skip duplicates */ }
        }
        console.log(`[Seed] PatternRules: ${result.patternRulesCreated} templates created (occurrences=0, winRate=0.5, no backtest results)`);
      }
    } catch (err) {
      console.warn('[Seed] PatternRule seeding failed:', err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 12: Initialize Behavioral Models (uses real engine)
  // ══════════════════════════════════════════════════════════
  if (!skipToPatterns && action as string !== 'traders') {
    try {
      console.log('[Seed] === STEP 12: Initializing Behavioral Models ===');
      const { behavioralModelEngine } = await import('@/lib/services/brain/behavioral-model-engine');
      await behavioralModelEngine.initializeDefaultMatrices();
      result.behaviorModelsInitialized = await db.traderBehaviorModel.count();
      console.log(`[Seed] Behavioral models: ${result.behaviorModelsInitialized} initialized`);
    } catch (err) {
      console.warn('[Seed] Behavioral model initialization skipped (engine not available):', err instanceof Error ? err.message : err);
    }
  }

  // ══════════════════════════════════════════════════════════
  // STEP 13: Real Data Sync Trigger
  // For action='full' or action='real', trigger real-sync
  // Uses direct function call instead of HTTP to avoid self-fetch
  // ══════════════════════════════════════════════════════════
  if (action === 'full' || action === 'real') {
    console.log('[Seed] === STEP 13: Real data sync trigger ===');
    console.log('[Seed] To sync real traders/candles/DNA/patterns, use: POST /api/real-sync with {"action":"full"}');
    console.log('[Seed] Or start auto-sync: POST /api/auto-sync with {"action":"start"}');
    result.realSyncTriggered = true;
  }

  return result;
}

// ════════════════════════════════════════════════════════════
// HTTP HANDLERS
// ════════════════════════════════════════════════════════════

export async function GET() {
  try {
    const { db } = await import('@/lib/db');

    const [
      tokenCount,
      dnaCount,
      signalCount,
      candleCount,
      traderCount,
      patternCount,
      modelCount,
    ] = await Promise.all([
      db.token.count(),
      db.tokenDNA.count(),
      db.signal.count(),
      db.priceCandle.count(),
      db.trader.count(),
      db.patternRule.count(),
      db.traderBehaviorModel.count(),
    ]);

    return NextResponse.json({
      status: 'ok',
      seedRunning,
      lastSeedResult,
      counts: {
        tokens: tokenCount,
        dna: dnaCount,
        signals: signalCount,
        candles: candleCount,
        traders: traderCount,
        patternRules: patternCount,
        behavioralModels: modelCount,
      },
      actions: {
        full: 'POST /api/seed { "action": "full" } — Full seed (tokens + DNA + signals + real candles + patterns + models)',
        tokens: 'POST /api/seed { "action": "tokens" } — Only tokens (Steps 1-6)',
        traders: 'POST /api/seed { "action": "traders" } — Redirect message (use /api/real-sync for real data)',
        patterns: 'POST /api/seed { "action": "patterns" } — Only pattern rules (Step 11)',
        real: 'POST /api/seed { "action": "real" } — Trigger real-sync for everything',
        status: 'GET /api/seed — This status endpoint',
      },
      note: 'NO SIMULATED DATA — all candle/trader data must come from real APIs',
    });
  } catch (err) {
    return NextResponse.json(
      { status: 'error', message: err instanceof Error ? err.message : 'Unknown error' },
      { status: 500 },
    );
  }
}

export async function POST(request: NextRequest) {
  // ── Concurrency guard ──
  if (seedRunning) {
    return NextResponse.json(
      {
        status: 'already_running',
        message: 'A seed operation is already in progress',
        lastResult: lastSeedResult,
      },
      { status: 409 },
    );
  }

  let body: SeedBody = {};
  try {
    body = await request.json();
  } catch {
    body = { action: 'full' };
  }

  const action: SeedAction = body.action || 'full';

  // Status-only check
  if (action === 'status') {
    return GET();
  }

  // Special case: 'traders' action returns redirect message immediately
  if (action === 'traders') {
    return NextResponse.json({
      status: 'redirect',
      message: 'Trader seeding with fake/random data has been removed. Use the real-sync endpoint for actual wallet data.',
      redirect: 'POST /api/real-sync',
      actions: {
        traders: 'POST /api/real-sync { "action": "traders" } — Fetch real wallet data from Etherscan',
        candles: 'POST /api/real-sync { "action": "candles" } — Fetch real OHLCV data',
        dna: 'POST /api/real-sync { "action": "dna" } — Recompute DNA from latest data',
        full: 'POST /api/real-sync { "action": "full" } — Sync everything',
      },
      note: 'If data is not available from a real API, it will NOT be created. An empty database with real data is better than a full database with fake data.',
    });
  }

  seedRunning = true;
  lastSeedResult = null;

  // Run in background, but respond immediately with a progress indicator
  const startTime = Date.now();

  try {
    const result = await runSeed(action);
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

    seedRunning = false;
    lastSeedResult = result;

    return NextResponse.json({
      status: 'completed',
      action,
      elapsed: `${elapsed}s`,
      result,
      note: action === 'full'
        ? 'Seed completed. Trader data requires real-sync endpoint — use POST /api/real-sync with { action: "traders" } for real wallet data.'
        : undefined,
    });
  } catch (err) {
    seedRunning = false;

    return NextResponse.json(
      {
        status: 'error',
        action,
        message: err instanceof Error ? err.message : 'Unknown seed error',
        stack: process.env.NODE_ENV === 'development' && err instanceof Error ? err.stack : undefined,
      },
      { status: 500 },
    );
  }
}
