import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** Timeout wrapper */
function withTimeout<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((resolve) => setTimeout(() => resolve(fallback), ms)),
  ]);
}

/**
 * GET /api/market/buy-sell-pressure
 *
 * Returns buy/sell pressure analysis for one or many pools on a chain.
 * Uses DexScreener's txn breakdown data (h24/h6/h1 buys/sells).
 *
 * Query params:
 *   chain  – chain identifier (default: "solana")
 *   poolId – optional; specific pool to analyse
 *            when omitted, pressure is returned for the top pools
 *
 * Response envelope:
 *   { pressure: BuySellPressure | BuySellPressure[], source: 'dexscreener' }
 */

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const chain = searchParams.get('chain') || 'solana';
  const poolId = searchParams.get('poolId') || undefined;

  try {
    const dpModule = await import('@/lib/services/data-sources/dexpaprika-client');
    const dexPaprikaClient = dpModule.dexPaprikaClient;
    type BuySellPressure = import('@/lib/services/data-sources/dexpaprika-client').BuySellPressure;
    // -----------------------------------------------------------
    // Single-pool mode
    // -----------------------------------------------------------
    if (poolId) {
      const pressure: BuySellPressure = await dexPaprikaClient.getBuySellPressure(chain, poolId);

      return NextResponse.json({
        pressure,
        source: 'dexscreener' as const,
      });
    }

    // -----------------------------------------------------------
    // Top-pools mode: try to get pools from DexScreener,
    // fallback to DB tokens with simulated pressure
    // -----------------------------------------------------------
    let pressureList: BuySellPressure[] = [];

    try {
      const result = await withTimeout(
        dexPaprikaClient.getPools(chain, 5),
        8000, // 8s timeout
        { pools: [] },
      );

      if (result.pools.length > 0) {
        pressureList = dexPaprikaClient.computePressureFromPools(result.pools);
      }
    } catch {
      // DexScreener unavailable - return empty with note
    }

    return NextResponse.json({
      pressure: pressureList,
      source: 'dexscreener' as const,
    });
  } catch (error) {
    console.error('[/api/market/buy-sell-pressure] Failed to fetch pressure:', error);
    return NextResponse.json(
      {
        pressure: null,
        source: 'dexscreener' as const,
        error: error instanceof Error ? error.message : 'Failed to fetch buy/sell pressure',
      },
      { status: 500 },
    );
  }
}
