import { NextRequest, NextResponse } from 'next/server';
import { strategyStateManager } from '@/lib/services/strategy/strategy-state-manager';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/strategy-states/[id]/history
 * Returns full state history for a specific strategy
 * Query params: startDate, endDate, limit
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const { searchParams } = new URL(request.url);
    const startDate = searchParams.get('startDate');
    const endDate = searchParams.get('endDate');
    const limit = Number(searchParams.get('limit')) || 100;

    const history = await strategyStateManager.getStrategyHistory(id, {
      startDate: startDate || undefined,
      endDate: endDate || undefined,
      limit,
    });

    return NextResponse.json({ data: history });
  } catch (error) {
    console.error('Error fetching strategy history:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch strategy history' },
      { status: 500 },
    );
  }
}
