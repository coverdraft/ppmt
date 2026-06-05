import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/regime?tokenAddress=X&chain=Y
 *
 * Returns current regime assessment for a token.
 * Uses the RegimeHeuristic to analyze PriceCandle data from the DB
 * and determine the market regime (TRENDING_UP, TRENDING_DOWN, SIDEWAYS,
 * HIGH_VOLATILITY, LOW_VOLATILITY).
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const tokenAddress = searchParams.get('tokenAddress');
    const chain = searchParams.get('chain') || 'SOL';

    if (!tokenAddress) {
      return NextResponse.json(
        { data: null, error: 'tokenAddress query parameter is required' },
        { status: 400 },
      );
    }

    const { regimeHeuristic } = await import('@/lib/services/strategy/regime-heuristic');
    const assessment = await regimeHeuristic.assessRegimeFromDB(tokenAddress, chain);

    return NextResponse.json({ data: assessment });
  } catch (error) {
    console.error('[Regime API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Regime assessment failed' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/regime
 *
 * Assess regime from a provided price array.
 * Body: { prices: number[] }
 *
 * Useful for assessing regime from external data sources
 * or from in-memory price data that hasn't been persisted to DB yet.
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { prices } = body as { prices?: number[] };

    if (!prices || !Array.isArray(prices) || prices.length === 0) {
      return NextResponse.json(
        { data: null, error: 'prices array is required and must not be empty' },
        { status: 400 },
      );
    }

    // Validate all elements are numbers
    const validPrices = prices.filter(p => typeof p === 'number' && isFinite(p));
    if (validPrices.length === 0) {
      return NextResponse.json(
        { data: null, error: 'prices array must contain valid numbers' },
        { status: 400 },
      );
    }

    const { regimeHeuristic } = await import('@/lib/services/strategy/regime-heuristic');
    const assessment = regimeHeuristic.assessRegime(validPrices);

    return NextResponse.json({ data: assessment });
  } catch (error) {
    console.error('[Regime API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Regime assessment failed' },
      { status: 500 },
    );
  }
}
