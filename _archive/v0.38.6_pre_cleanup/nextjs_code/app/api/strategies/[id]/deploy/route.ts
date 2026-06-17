import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const dynamic = 'force-dynamic';

const LIFECYCLE_ORDER = ['draft', 'backtesting', 'paper_trading', 'forward_testing', 'live'];

// POST /api/strategies/[id]/deploy - Promote strategy to next lifecycle stage
export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    const strategy = await db.pPMTStrategy.findUnique({
      where: { id },
    });

    if (!strategy) {
      return NextResponse.json(
        { success: false, error: 'Strategy not found' },
        { status: 404 }
      );
    }

    const currentIndex = LIFECYCLE_ORDER.indexOf(strategy.status);
    if (currentIndex === -1) {
      return NextResponse.json(
        { success: false, error: `Invalid status: ${strategy.status}` },
        { status: 400 }
      );
    }

    if (currentIndex >= LIFECYCLE_ORDER.length - 1) {
      return NextResponse.json(
        { success: false, error: 'Strategy is already at the final stage (live)' },
        { status: 400 }
      );
    }

    const nextStatus = LIFECYCLE_ORDER[currentIndex + 1];

    // When promoting to live, allocate capital if not set
    const updateData: Record<string, unknown> = {
      status: nextStatus,
      lastRunAt: new Date(),
    };

    if (nextStatus === 'live' && strategy.capitalAllocated === 0) {
      updateData.capitalAllocated = strategy.initialCapital;
    }

    // When promoting to backtesting from draft, simulate some results
    if (nextStatus === 'backtesting') {
      // Results will be filled by actual backtest run
    }

    const updated = await db.pPMTStrategy.update({
      where: { id },
      data: updateData,
    });

    // Create a run record for this promotion
    const runTypeMap: Record<string, string> = {
      backtesting: 'backtest',
      paper_trading: 'paper_trading',
      forward_testing: 'forward_test',
      live: 'live',
    };

    await db.pPMTStrategyRun.create({
      data: {
        strategyId: id,
        runType: runTypeMap[nextStatus] || nextStatus,
        status: 'running',
      },
    });

    return NextResponse.json({
      success: true,
      data: updated,
      message: `Strategy promoted from ${strategy.status} → ${nextStatus}`,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to deploy strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
