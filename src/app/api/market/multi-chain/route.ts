import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { normalizeChain, getChainVariants } from '@/lib/format';
import { dexScreenerClient, type DexScreenerPair } from '@/lib/services/data-sources/dexscreener-client';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// TYPES
// ============================================================

interface ChainSummary {
  tokenCount: number;
  totalVolume24h: number;
  avgPriceChange24h: number;
  topGainer: { symbol: string; priceChange24h: number; priceUsd: number } | null;
  topLoser: { symbol: string; priceChange24h: number; priceUsd: number } | null;
}

interface CrossChainToken {
  symbol: string;
  chains: string[];
  priceByChain: Record<string, number>;
  volumeByChain: Record<string, number>;
  priceDeviationPct: number;
}

interface TopTokenEntry {
  symbol: string;
  name: string;
  priceUsd: number;
  volume24h: number;
  priceChange24h: number;
  marketCap: number;
  chain: string;
}

interface ChainRankingEntry {
  chain: string;
  rank: number;
  totalVolume24h: number;
  avgChange24h: number;
  tokenCount: number;
  topTokens: TopTokenEntry[];
}

interface MultiChainResponse {
  chainSummary: Record<string, ChainSummary>;
  crossChainTokens: CrossChainToken[];
  topTokensByChain: Record<string, TopTokenEntry[]>;
  chainRanking: ChainRankingEntry[];
}

// ============================================================
// CHAIN NORMALIZATION
// ============================================================

// Use the shared normalizeChain from @/lib/format
const normalizeChainKey = normalizeChain;

/** Map our standard chain keys back to DB chain values for querying.
 *  Uses getChainVariants to catch legacy data stored under 'SOLANA', 'ETHEREUM', etc.
 */
function getDbChainValues(chainKey: string): string[] {
  return getChainVariants(chainKey);
}

const SUPPORTED_CHAINS = ['SOL', 'ETH', 'BASE', 'BSC', 'MATIC', 'ARB', 'OP', 'AVAX'];

/** Map our canonical chain keys to DexScreener search terms */
const CHAIN_TO_DEXSCREENER_QUERY: Record<string, string> = {
  SOL: 'solana',
  ETH: 'ethereum',
  BSC: 'bsc',
  BASE: 'base',
  ARB: 'arbitrum',
  MATIC: 'polygon',
  AVAX: 'avalanche',
  OP: 'optimism',
};

const MIN_TOKENS_PER_CHAIN = 5;

// ============================================================
// DEXSCREENER ENRICHMENT
// ============================================================

/** Convert a DexScreener pair to a normalized token shape that matches DB tokens */
function dexPairToToken(pair: DexScreenerPair) {
  const chainKey = normalizeChain(pair.chainId);
  return {
    id: `dex-${pair.pairAddress}`,
    symbol: (pair.baseToken?.symbol || '').toUpperCase(),
    name: pair.baseToken?.name || '',
    chain: chainKey,
    priceUsd: parseFloat(pair.priceUsd || '0') || 0,
    volume24h: pair.volume?.h24 || 0,
    marketCap: pair.marketCap || pair.fdv || 0,
    liquidity: pair.liquidity?.usd || 0,
    priceChange5m: pair.priceChange?.m5 || 0,
    priceChange15m: pair.priceChange?.m15 || 0,
    priceChange1h: pair.priceChange?.h1 || 0,
    priceChange6h: pair.priceChange?.h6 || 0,
    priceChange24h: pair.priceChange?.h24 || 0,
  };
}

/** Fetch tokens from DexScreener for chains that have insufficient DB data */
async function enrichFromDexScreener(
  chainsNeedingEnrichment: string[],
): Promise<typeof normalizedTokens> {
  const enriched: typeof normalizedTokens = [];

  for (const chainKey of chainsNeedingEnrichment) {
    const query = CHAIN_TO_DEXSCREENER_QUERY[chainKey];
    if (!query) continue;

    try {
      console.log(`[/api/market/multi-chain] Enriching ${chainKey} from DexScreener (query: "${query}")`);
      const pairs = await dexScreenerClient.searchTokenByName(query);
      // Filter to only pairs on this chain
      const chainNorm = dexScreenerClient.normalizeChain(query);
      const filtered = pairs.filter(p => dexScreenerClient.normalizeChain(p.chainId) === chainNorm);

      // Sort by volume, take top 20
      filtered.sort((a, b) => (b.volume?.h24 || 0) - (a.volume?.h24 || 0));
      const topPairs = filtered.slice(0, 20);

      for (const pair of topPairs) {
        const token = dexPairToToken(pair);
        if (SUPPORTED_CHAINS.includes(token.chain)) {
          enriched.push(token as any);
        }
      }
    } catch (err) {
      console.warn(`[/api/market/multi-chain] DexScreener enrichment failed for ${chainKey}:`, err);
    }
  }

  return enriched;
}

// ============================================================
// GET HANDLER
// ============================================================

export async function GET(request: NextRequest) {
  const sp = request.nextUrl.searchParams;

  // Parse query params
  const chainsParam = sp.get('chains') || '';  // empty = all
  const metric = sp.get('metric') || 'volume';  // volume | marketCap | priceChange | trades
  const topN = Math.min(Math.max(parseInt(sp.get('topN') || '10', 10), 1), 50);
  const includeCrossChain = sp.get('includeCrossChain') !== 'false';

  // Determine which chains to include
  const requestedChains = chainsParam
    ? chainsParam.split(',').map(normalizeChainKey).filter((c): c is string => SUPPORTED_CHAINS.includes(c))
    : SUPPORTED_CHAINS;

  if (requestedChains.length === 0) {
    return NextResponse.json({ error: 'No valid chains specified' }, { status: 400 });
  }

  try {
    // Build DB filter for all requested chains
    const chainValues = requestedChains.flatMap(getDbChainValues);
    const uniqueChainValues = [...new Set(chainValues)];

    // Fetch all tokens for the requested chains from DB
    const dbTokens = await db.token.findMany({
      where: {
        chain: { in: uniqueChainValues },
        volume24h: { gt: 0 },
      },
      orderBy: { volume24h: 'desc' },
      take: 500,
    });

    // Normalize token chain to our standard keys
    const normalizedTokens = dbTokens.map(t => ({
      ...t,
      chain: normalizeChainKey(t.chain),
    })).filter(t => SUPPORTED_CHAINS.includes(t.chain));

    // ============================================================
    // DEXSCREENER ENRICHMENT: Fill gaps where DB has < 5 tokens
    // ============================================================
    const tokensPerChain: Record<string, number> = {};
    for (const chain of requestedChains) {
      tokensPerChain[chain] = normalizedTokens.filter(t => t.chain === chain).length;
    }

    const chainsNeedingEnrichment = requestedChains.filter(
      chain => tokensPerChain[chain] < MIN_TOKENS_PER_CHAIN
    );

    let enrichmentSource: string | null = null;
    if (chainsNeedingEnrichment.length > 0) {
      const enriched = await enrichFromDexScreener(chainsNeedingEnrichment);

      // Merge: add enriched tokens that don't already exist by symbol+chain
      const existingKeys = new Set(
        normalizedTokens.map(t => `${t.symbol.toUpperCase()}-${t.chain}`)
      );
      for (const token of enriched) {
        const key = `${token.symbol.toUpperCase()}-${token.chain}`;
        if (!existingKeys.has(key)) {
          normalizedTokens.push(token);
          existingKeys.add(key);
        }
      }

      if (enriched.length > 0) {
        enrichmentSource = `dexscreener (${chainsNeedingEnrichment.join(',')})`;
      }
    }

    // ============================================================
    // 1. CHAIN SUMMARY
    // ============================================================
    const chainSummary: Record<string, ChainSummary> = {};
    const tokensByChain: Record<string, typeof normalizedTokens> = {};

    for (const chain of requestedChains) {
      const tokens = normalizedTokens.filter(t => t.chain === chain);
      tokensByChain[chain] = tokens;

      const totalVolume = tokens.reduce((s, t) => s + t.volume24h, 0);
      const avgChange = tokens.length > 0
        ? tokens.reduce((s, t) => s + t.priceChange24h, 0) / tokens.length
        : 0;

      // Find top gainer & loser
      const sortedByChange = [...tokens].sort((a, b) => b.priceChange24h - a.priceChange24h);
      const topGainer = sortedByChange.length > 0 && sortedByChange[0].priceChange24h > 0
        ? { symbol: sortedByChange[0].symbol, priceChange24h: sortedByChange[0].priceChange24h, priceUsd: sortedByChange[0].priceUsd }
        : null;
      const topLoser = sortedByChange.length > 0 && sortedByChange[sortedByChange.length - 1].priceChange24h < 0
        ? { symbol: sortedByChange[sortedByChange.length - 1].symbol, priceChange24h: sortedByChange[sortedByChange.length - 1].priceChange24h, priceUsd: sortedByChange[sortedByChange.length - 1].priceUsd }
        : null;

      chainSummary[chain] = {
        tokenCount: tokens.length,
        totalVolume24h: totalVolume,
        avgPriceChange24h: avgChange,
        topGainer,
        topLoser,
      };
    }

    // ============================================================
    // 2. TOP TOKENS BY CHAIN (sorted by requested metric)
    // ============================================================
    const topTokensByChain: Record<string, TopTokenEntry[]> = {};

    for (const chain of requestedChains) {
      const tokens = tokensByChain[chain] || [];

      let sorted: typeof tokens;
      switch (metric) {
        case 'marketCap':
          sorted = [...tokens].sort((a, b) => b.marketCap - a.marketCap);
          break;
        case 'priceChange':
          sorted = [...tokens].sort((a, b) => Math.abs(b.priceChange24h) - Math.abs(a.priceChange24h));
          break;
        case 'trades':
          // Use volume as proxy for trade count
          sorted = [...tokens].sort((a, b) => b.volume24h - a.volume24h);
          break;
        case 'volume':
        default:
          sorted = [...tokens].sort((a, b) => b.volume24h - a.volume24h);
          break;
      }

      topTokensByChain[chain] = sorted.slice(0, topN).map(t => ({
        symbol: t.symbol,
        name: t.name,
        priceUsd: t.priceUsd,
        volume24h: t.volume24h,
        priceChange24h: t.priceChange24h,
        marketCap: t.marketCap,
        chain: t.chain,
      }));
    }

    // ============================================================
    // 3. CROSS-CHAIN TOKENS (same symbol on multiple chains)
    // ============================================================
    let crossChainTokens: CrossChainToken[] = [];

    if (includeCrossChain) {
      // Group all tokens by uppercase symbol
      const symbolMap: Record<string, typeof normalizedTokens> = {};
      for (const token of normalizedTokens) {
        const sym = token.symbol.toUpperCase();
        if (!symbolMap[sym]) symbolMap[sym] = [];
        symbolMap[sym].push(token);
      }

      // Filter to symbols appearing on 2+ chains
      crossChainTokens = Object.entries(symbolMap)
        .filter(([, tokens]) => {
          const chains = new Set(tokens.map(t => t.chain));
          return chains.size >= 2;
        })
        .map(([symbol, tokens]) => {
          const chains = [...new Set(tokens.map(t => t.chain))] as string[];
          const priceByChain: Record<string, number> = {};
          const volumeByChain: Record<string, number> = {};

          // Pick the best (highest volume) token per chain
          for (const chain of chains) {
            const chainTokens = tokens.filter(t => t.chain === chain);
            chainTokens.sort((a, b) => b.volume24h - a.volume24h);
            priceByChain[chain] = chainTokens[0].priceUsd;
            volumeByChain[chain] = chainTokens[0].volume24h;
          }

          // Calculate price deviation (% standard deviation / mean)
          const prices = Object.values(priceByChain).filter(p => p > 0);
          let priceDeviationPct = 0;
          if (prices.length >= 2) {
            const mean = prices.reduce((s, p) => s + p, 0) / prices.length;
            if (mean > 0) {
              const variance = prices.reduce((s, p) => s + Math.pow(p - mean, 2), 0) / prices.length;
              priceDeviationPct = (Math.sqrt(variance) / mean) * 100;
            }
          }

          return { symbol, chains, priceByChain, volumeByChain, priceDeviationPct };
        })
        .sort((a, b) => b.priceDeviationPct - a.priceDeviationPct)
        .slice(0, 30);
    }

    // ============================================================
    // 4. CHAIN RANKING
    // ============================================================
    const chainRanking: ChainRankingEntry[] = requestedChains
      .map(chain => {
        const summary = chainSummary[chain];
        const topTokens = (topTokensByChain[chain] || []).slice(0, 3);
        return {
          chain,
          rank: 0,
          totalVolume24h: summary.totalVolume24h,
          avgChange24h: summary.avgPriceChange24h,
          tokenCount: summary.tokenCount,
          topTokens,
        };
      })
      .sort((a, b) => b.totalVolume24h - a.totalVolume24h)
      .map((entry, i) => ({ ...entry, rank: i + 1 }));

    // Build response
    const response: MultiChainResponse = {
      chainSummary,
      crossChainTokens,
      topTokensByChain,
      chainRanking,
    };

    return NextResponse.json({
      data: response,
      error: null,
      source: enrichmentSource ? `db+${enrichmentSource}` : 'db',
      chains: requestedChains,
      metric,
      topN,
      enrichment: enrichmentSource ? { enrichedChains: chainsNeedingEnrichment, source: 'dexscreener' } : null,
    });
  } catch (error) {
    console.error('[/api/market/multi-chain] Error:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch multi-chain data', source: 'error' },
      { status: 500 },
    );
  }
}
