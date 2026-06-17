import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/search
 *
 * Searches tokens by name or symbol using the DexScreener search API.
 *
 * Query params:
 *   q     – search query (required, min 2 chars)
 *   chain – filter by chain id, e.g. "solana", "ethereum" (optional)
 *
 * Rate-limit notes (free tiers):
 *   - DexScreener: ~300 req/min (unauthenticated)
 *
 * Response envelope:
 *   { data: SearchResult[] | null, error: string | null, source: 'live' | 'cache' | 'fallback' }
 */

// Use canonical DexScreenerClient singleton
import { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
import type { DexScreenerPair } from '@/lib/services/data-sources/dexscreener-client';

interface SearchResult {
  id: string;
  symbol: string;
  name: string;
  chain: string;
  address: string;
  priceUsd: number;
  volume24h: number;
  liquidity: number;
  marketCap: number;
  priceChange24h: number;
  dexId: string;
  pairAddress: string;
  pairUrl: string;
  imageUrl?: string;
}

function formatResult(pair: DexScreenerPair): SearchResult {
  return {
    id: pair.pairAddress,
    symbol: pair.baseToken.symbol,
    name: pair.baseToken.name,
    chain: pair.chainId,
    address: pair.baseToken.address,
    priceUsd: parseFloat(pair.priceUsd) || 0,
    volume24h: pair.volume.h24 ?? 0,
    liquidity: pair.liquidity?.usd ?? 0,
    marketCap: pair.marketCap ?? pair.fdv ?? 0,
    priceChange24h: 0, // DexScreener search doesn't include price change in pair results
    dexId: pair.dexId,
    pairAddress: pair.pairAddress,
    pairUrl: `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}`,
    imageUrl: pair.info?.imageUrl,
  };
}

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const query = searchParams.get('q')?.trim();
  const chain = searchParams.get('chain') || undefined;

  if (!query || query.length < 2) {
    return NextResponse.json(
      { data: null, error: 'Search query must be at least 2 characters', source: 'live' as const },
      { status: 400 },
    );
  }

  try {
    const ds = dexScreenerClient;
    const { db } = await import('@/lib/db');
    const pairs = await ds.searchTokenByName(query);

    // Apply optional chain filter
    const filtered = chain
      ? pairs.filter((p) => p.chainId === chain)
      : pairs;

    // Deduplicate by base token address
    const seen = new Set<string>();
    const results: SearchResult[] = [];

    for (const pair of filtered) {
      const key = pair.baseToken.address.toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        results.push(formatResult(pair));
      }
    }

    return NextResponse.json({
      data: results,
      error: null,
      source: 'live' as const,
    });
  } catch (error) {
    console.error('[/api/market/search] DexScreener search failed, falling back to DB:', error);

    // -----------------------------------------------------------
    // Fallback: search local database by symbol / name
    // -----------------------------------------------------------
    try {
      const { db: dbSearch } = await import('@/lib/db');
      const dbTokens = await dbSearch.token.findMany({
        where: {
          ...(chain ? { chain: chain.toUpperCase() } : {}),
          OR: [
            { symbol: { contains: query } },
            { name: { contains: query } },
            { address: { contains: query } },
          ],
        },
        include: { dna: true },
        orderBy: { volume24h: 'desc' },
        take: 50,
      });

      const results: SearchResult[] = dbTokens.map((t) => ({
        id: t.id,
        symbol: t.symbol,
        name: t.name,
        chain: t.chain,
        address: t.address,
        priceUsd: t.priceUsd,
        volume24h: t.volume24h,
        liquidity: t.liquidity,
        marketCap: t.marketCap,
        priceChange24h: t.priceChange24h,
        dexId: t.dexId ?? '',
        pairAddress: t.pairAddress ?? '',
        pairUrl: t.pairUrl ?? '',
      }));

      return NextResponse.json({
        data: results,
        error: null,
        source: 'fallback' as const,
      });
    } catch (dbError) {
      console.error('[/api/market/search] DB fallback also failed:', dbError);
      return NextResponse.json(
        {
          data: null,
          error: 'Search failed from live source and database',
          source: 'fallback' as const,
        },
        { status: 500 },
      );
    }
  }
}
