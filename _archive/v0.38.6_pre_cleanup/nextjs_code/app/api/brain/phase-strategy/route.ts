import { NextResponse } from 'next/server';

/**
 * GET /api/brain/phase-strategy
 *
 * Returns Phase Strategy Report with strategies per trading stage
 * (EARLY/MID/STABLE), token distribution, and top opportunities.
 */
export async function GET() {
  try {
    const { phaseStrategyEngine } = await import('@/lib/services/brain/phase-strategy-engine');
    const report = await phaseStrategyEngine.generateReport();
    return NextResponse.json({ success: true, data: report });
  } catch (error: any) {
    console.error('[/api/brain/phase-strategy] Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
