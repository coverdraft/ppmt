import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

/**
 * GET /api/backtest/[id]
 * Get a single backtest with all operations and system info.
 */
export async function GET(
  _request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;

    const backtest = await db.backtestRun.findUnique({
      where: { id },
      include: {
        system: {
          select: {
            id: true,
            name: true,
            category: true,
            icon: true,
            primaryTimeframe: true,
            allocationMethod: true,
          },
        },
        operations: {
          orderBy: { entryTime: 'asc' },
        },
      },
    });

    if (!backtest) {
      return NextResponse.json(
        { data: null, error: 'Backtest not found' },
        { status: 404 },
      );
    }

    return NextResponse.json({ data: backtest });
  } catch (error) {
    console.error('Error getting backtest:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get backtest' },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/backtest/[id]
 * Delete a backtest run and all its operations.
 */
export async function DELETE(
  _request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;

    // Check if backtest exists
    const existing = await db.backtestRun.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { data: null, error: 'Backtest not found' },
        { status: 404 },
      );
    }

    // Check if it's currently running
    if (existing.status === 'RUNNING') {
      return NextResponse.json(
        { data: null, error: 'Cannot delete a running backtest. Wait for it to complete or fail first.' },
        { status: 409 },
      );
    }

    // Delete operations first, then the backtest
    await db.backtestOperation.deleteMany({ where: { backtestId: id } });
    await db.backtestRun.delete({ where: { id } });

    return NextResponse.json({ data: { id, deleted: true } });
  } catch (error) {
    console.error('Error deleting backtest:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to delete backtest' },
      { status: 500 },
    );
  }
}
