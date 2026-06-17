import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/brain/pipeline
 * Run a brain cycle with optional config overrides.
 * Uses the canonical BrainCycleEngine (migrated from legacy brain-pipeline).
 */
export async function POST(request: NextRequest) {
  try {
    const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
    let body: Record<string, unknown> = {};
    try {
      body = await request.json();
    } catch {
      // No JSON body provided, use defaults
    }
    const config: Partial<import('@/lib/services/brain/brain-cycle-engine').CycleConfig> = {
      ...(body.capitalUsd !== undefined && { capitalUsd: Number(body.capitalUsd) }),
      ...(body.chain !== undefined && { chain: String(body.chain) }),
      ...(body.scanLimit !== undefined && { scanLimit: Number(body.scanLimit) }),
      ...(body.cycleIntervalMs !== undefined && { cycleIntervalMs: Number(body.cycleIntervalMs) }),
      ...(body.expectedGainPct !== undefined && { expectedGainPct: Number(body.expectedGainPct) }),
    };

    const result = await brainCycleEngine.start(config);

    return NextResponse.json({
      data: result,
      error: null,
    }, { status: result.started ? 200 : 500 });
  } catch (error) {
    console.error('[/api/brain/pipeline] Error:', error);
    const errorMsg = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { data: null, error: `Pipeline execution failed: ${errorMsg}` },
      { status: 500 },
    );
  }
}

/**
 * GET /api/brain/pipeline
 * Get current brain cycle status and growth report.
 * Uses BrainCycleEngine (migrated from legacy brain-pipeline).
 */
export async function GET() {
  try {
    const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
    const [status, growthReport] = await Promise.all([
      Promise.resolve(brainCycleEngine.getStatus()),
      brainCycleEngine.getGrowthReport().catch(() => null),
    ]);

    return NextResponse.json({
      data: {
        status,
        growthReport,
      },
      error: null,
    });
  } catch (error) {
    console.error('[/api/brain/pipeline] GET error:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get pipeline state' },
      { status: 500 },
    );
  }
}
