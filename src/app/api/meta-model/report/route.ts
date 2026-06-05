import { NextResponse } from 'next/server';
import { metaModelEngine } from '@/lib/services/brain/meta-model-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/meta-model/report
 * Get meta-model engine report (all engine accuracies and weights).
 * Response: EngineReport[]
 */
export async function GET() {
  try {
    const reports = await metaModelEngine.getEngineReport();

    const serialized = reports.map(report => ({
      engineName: report.engineName,
      overall: report.overall,
      rolling: report.rolling,
      contextual: {
        byRegime: report.contextual.byRegime,
        byPhase: report.contextual.byPhase,
      },
      currentWeight: report.currentWeight,
      weightChange: report.weightChange,
    }));

    return NextResponse.json({ data: serialized });
  } catch (error) {
    console.error('Error getting meta-model report:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get meta-model report' },
      { status: 500 }
    );
  }
}
