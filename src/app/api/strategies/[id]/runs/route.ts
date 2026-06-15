import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const dynamic = 'force-dynamic';

// GET /api/strategies/[id]/runs - List runs for a strategy
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    const runs = await db.pPMTStrategyRun.findMany({
      where: { strategyId: id },
      orderBy: { startedAt: 'desc' },
    });

    return NextResponse.json({ success: true, data: runs });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to fetch runs';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
