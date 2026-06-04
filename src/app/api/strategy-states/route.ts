import { NextRequest, NextResponse } from 'next/server';
import { strategyStateManager, type StrategyStatus, type StateStatistics } from '@/lib/services/strategy/strategy-state-manager';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/strategy-states
 * Returns all trading systems with their current state and history
 * Query params: status, category, includeStats
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const status = searchParams.get('status') as StrategyStatus | null;
    const category = searchParams.get('category');
    const includeStats = searchParams.get('includeStats') === 'true';

    const strategies = await strategyStateManager.getCurrentStates({
      status: status || undefined,
      category: category || undefined,
    });

    let statistics: StateStatistics | null = null;
    if (includeStats) {
      statistics = await strategyStateManager.getStateStatistics();
    }

    return NextResponse.json({
      data: strategies,
      statistics,
    });
  } catch (error) {
    console.error('Error fetching strategy states:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch strategy states' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/strategy-states
 * Record a state transition
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { systemId, newStatus, triggerReason, metadata, metrics, evolution } = body;

    if (!systemId || !newStatus || !triggerReason) {
      return NextResponse.json(
        { data: null, error: 'Missing required fields: systemId, newStatus, triggerReason' },
        { status: 400 },
      );
    }

    const record = await strategyStateManager.recordStateTransition({
      systemId,
      newStatus,
      triggerReason,
      metadata,
      metrics,
      evolution,
    });

    return NextResponse.json({ data: record }, { status: 201 });
  } catch (error) {
    console.error('Error recording state transition:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to record state transition' },
      { status: 500 },
    );
  }
}
