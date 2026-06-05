import { NextRequest, NextResponse } from 'next/server';
import { alphaRankingEngine, type RankedOpportunity } from '@/lib/services/strategy/alpha-ranking-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/alpha/ranking
 * Get top alpha opportunities.
 * Query: ?n=5
 * Response: RankedOpportunity[]
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const n = parseInt(searchParams.get('n') || '5', 10);

    const ranked: RankedOpportunity[] = await alphaRankingEngine.getTopOpportunities(
      Math.max(1, Math.min(n, 20))
    );

    // Serialize for JSON — strip any non-serializable fields
    const serialized = ranked.map(opp => ({
      tokenAddress: opp.tokenAddress,
      chain: opp.chain,
      direction: opp.direction,
      confidence: opp.confidence,
      strategyName: opp.strategyName,
      expectedReturn: opp.expectedReturn,
      expectedVol: opp.expectedVol,
      operabilityScore: opp.operabilityScore,
      regimeFit: opp.regimeFit,
      tokenPhase: opp.tokenPhase,
      regime: opp.regime,
      liquidityUsd: opp.liquidityUsd,
      volume24h: opp.volume24h,
      alphaScore: opp.alphaScore,
      rank: opp.rank,
      scoreBreakdown: opp.scoreBreakdown,
      suggestedAllocationPct: opp.suggestedAllocationPct,
    }));

    return NextResponse.json({ data: serialized });
  } catch (error) {
    console.error('Error getting alpha ranking:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get alpha ranking' },
      { status: 500 }
    );
  }
}
