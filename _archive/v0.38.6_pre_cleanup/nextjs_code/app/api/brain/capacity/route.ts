import { NextResponse } from 'next/server';

/**
 * GET /api/brain/capacity
 *
 * Returns Brain Capacity Report with data metrics, readiness indicators,
 * and analysis capabilities.
 */
export async function GET() {
  try {
    const { brainCapacityEngine } = await import('@/lib/services/brain/brain-capacity-engine');
    const report = await brainCapacityEngine.generateReport();
    return NextResponse.json({ success: true, data: report });
  } catch (error: any) {
    console.error('[/api/brain/capacity] Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
