import { NextResponse } from 'next/server';
import { riskControlsVerifier } from '@/lib/services/risk/risk-controls-verifier';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/portfolio/risk-verification
 *
 * Returns the risk controls verification result.
 * Diagnostic tool that checks all risk control points are properly enforced.
 */
export async function GET() {
  try {
    const result = await riskControlsVerifier.verifyRiskControls();
    return NextResponse.json({ data: result });
  } catch (error) {
    console.error('[Risk Verification API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Risk verification failed' },
      { status: 500 },
    );
  }
}
