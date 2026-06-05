import { NextRequest, NextResponse } from 'next/server';
import { strategyDecisionEngine } from '@/lib/services/strategy/strategy-decision-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/strategy-decision/audit
 *
 * Query SDE audit records.
 *
 * Query params:
 *   strategyId=xxx  (optional, filter by strategy)
 *   from=2024-01-01  (optional, ISO date)
 *   to=2024-12-31    (optional, ISO date)
 *   limit=50          (optional, max records)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);

    const strategyId = searchParams.get('strategyId') || undefined;
    const from = searchParams.get('from') ? new Date(searchParams.get('from')!) : undefined;
    const to = searchParams.get('to') ? new Date(searchParams.get('to')!) : undefined;
    const rawLimit = parseInt(searchParams.get('limit') || '50', 10);
    const limit = Number.isNaN(rawLimit) ? 50 : Math.min(Math.max(rawLimit, 1), 200);

    const records = await strategyDecisionEngine.queryAudit({
      strategyId,
      from,
      to,
      limit,
    });

    return NextResponse.json({ data: records });
  } catch (error) {
    console.error('[SDE Audit API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Audit query failed' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/strategy-decision/audit
 *
 * Provide feedback on a previous decision.
 *
 * Body: { auditId: string, wasCorrect: boolean, realizedPnlPct: number }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    if (!body.auditId || typeof body.wasCorrect !== 'boolean') {
      return NextResponse.json(
        { data: null, error: 'auditId and wasCorrect (boolean) are required' },
        { status: 400 },
      );
    }

    await strategyDecisionEngine.provideFeedback(
      body.auditId,
      body.wasCorrect,
      body.realizedPnlPct ?? 0,
    );

    return NextResponse.json({ data: { auditId: body.auditId, feedbackRecorded: true } });
  } catch (error) {
    console.error('[SDE Audit API] Feedback error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Feedback failed' },
      { status: 500 },
    );
  }
}
