import { NextRequest, NextResponse } from 'next/server';
import { strategyStateManager, type StrategyStatus } from '@/lib/services/strategy/strategy-state-manager';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * PUT /api/strategy-states/{id}
 * Update a strategy's state (pause, resume, force transition)
 * Body: { status: StrategyStatus, triggerReason?: string, metadata?: Record<string, unknown> }
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = await request.json();
    const { status, triggerReason, metadata } = body;

    if (!status) {
      return NextResponse.json(
        { data: null, error: 'Missing required field: status' },
        { status: 400 },
      );
    }

    const validStatuses: StrategyStatus[] = [
      'IDLE', 'BACKTESTING', 'PAPER_TRADING', 'LIVE', 'PAUSED', 'ERROR', 'EVOLVED',
    ];

    if (!validStatuses.includes(status as StrategyStatus)) {
      return NextResponse.json(
        { data: null, error: `Invalid status. Must be one of: ${validStatuses.join(', ')}` },
        { status: 400 },
      );
    }

    const record = await strategyStateManager.recordStateTransition({
      systemId: id,
      newStatus: status as StrategyStatus,
      triggerReason: triggerReason || 'MANUAL',
      metadata,
    });

    return NextResponse.json({ data: record });
  } catch (error) {
    console.error('Error updating strategy state:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to update strategy state' },
      { status: 500 },
    );
  }
}
