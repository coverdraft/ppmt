import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/pools
 *
 * Fetches top liquidity pools via DexScreener, or searches for pools
 * when a `q` query parameter is supplied.
 * Pool data includes buy/sell txn ratios from DexScreener.
 *
 * Query params:
 *   chain  – filter by chain (default: "solana")
 *   limit  – max pools to return (default: 30)
 *   cursor – pagination cursor from a previous response
 *   q      – search query; when provided, uses searchPools instead of getPools
 *
 * Response envelope:
 *   { pools, cursor?, source: 'dexscreener' }
 */

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const chain = searchParams.get('chain') || 'solana';
  const limit = Math.min(parseInt(searchParams.get('limit') || '30', 10), 100);
  const cursor = searchParams.get('cursor') || undefined;
  const query = searchParams.get('q') || undefined;

  try {
    const dpModule = await import('@/lib/services/data-sources/dexpaprika-client');
    const dexPaprikaClient = dpModule.dexPaprikaClient;
    // -----------------------------------------------------------
    // Search mode: user supplied a `q` param
    // -----------------------------------------------------------
    if (query) {
      const pools = await dexPaprikaClient.searchPools({
        query,
        chain,
        limit,
      });

      return NextResponse.json({
        pools,
        cursor: undefined,
        source: 'dexscreener' as const,
      });
    }

    // -----------------------------------------------------------
    // Top-pools mode (default)
    // -----------------------------------------------------------
    const data = await dexPaprikaClient.getPools(chain, limit, cursor);

    return NextResponse.json({
      pools: data.pools,
      cursor: data.cursor ?? undefined,
      source: 'dexscreener' as const,
    });
  } catch (error) {
    console.error('[/api/market/pools] Failed to fetch pools:', error);
    return NextResponse.json(
      {
        pools: [],
        cursor: undefined,
        source: 'dexscreener' as const,
        error: error instanceof Error ? error.message : 'Failed to fetch pools',
      },
      { status: 500 },
    );
  }
}
