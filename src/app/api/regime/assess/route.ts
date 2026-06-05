import { NextRequest, NextResponse } from 'next/server';
import { marketRegimeEngine } from '@/lib/services/strategy/market-regime-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/regime/assess
 * Get current market regime assessment.
 * Query: ?tokenAddress=xxx&chain=SOL
 * Response: RegimeAssessment
 */
export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const tokenAddress = searchParams.get('tokenAddress') || undefined;
    const chain = searchParams.get('chain') || 'SOL';

    const assessment = await marketRegimeEngine.assessRegime(tokenAddress, chain);

    // Serialize Map for JSON response
    const transitionProbabilities: Record<string, number> = {};
    for (const [regime, prob] of assessment.transitionProbabilities) {
      transitionProbabilities[regime] = prob;
    }

    return NextResponse.json({
      data: {
        regime: assessment.regime,
        confidence: assessment.confidence,
        transitionProbabilities,
        durationEstimate: assessment.durationEstimate,
        keyIndicators: assessment.keyIndicators,
        lastChangedAt: assessment.lastChangedAt.toISOString(),
        assessedAt: assessment.assessedAt.toISOString(),
      },
    });
  } catch (error) {
    console.error('Error assessing market regime:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to assess market regime' },
      { status: 500 }
    );
  }
}
