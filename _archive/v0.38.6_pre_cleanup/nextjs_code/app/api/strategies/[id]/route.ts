import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const dynamic = 'force-dynamic';

// GET /api/strategies/[id] - Get a single strategy
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const strategy = await db.pPMTStrategy.findUnique({
      where: { id },
      include: {
        runs: {
          orderBy: { startedAt: 'desc' },
        },
      },
    });

    if (!strategy) {
      return NextResponse.json(
        { success: false, error: 'Strategy not found' },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true, data: strategy });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to fetch strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

// PUT /api/strategies/[id] - Update a strategy
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await request.json();

    const strategy = await db.pPMTStrategy.update({
      where: { id },
      data: body,
    });

    return NextResponse.json({ success: true, data: strategy });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to update strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

// DELETE /api/strategies/[id] - Delete a strategy
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;

    // Delete runs first
    await db.pPMTStrategyRun.deleteMany({
      where: { strategyId: id },
    });

    await db.pPMTStrategy.delete({
      where: { id },
    });

    return NextResponse.json({ success: true });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to delete strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
