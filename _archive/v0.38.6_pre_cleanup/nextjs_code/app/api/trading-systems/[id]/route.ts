import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

/**
 * GET /api/trading-systems/[id]
 * Get a single trading system with all relations (backtests, operations, derivedSystems).
 */
export async function GET(
  _request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;

    const tradingSystem = await db.tradingSystem.findUnique({
      where: { id },
      include: {
        backtests: {
          orderBy: { createdAt: 'desc' },
          take: 50,
        },
        operations: {
          orderBy: { createdAt: 'desc' },
          take: 100,
        },
        derivedSystems: true,
        parentSystem: {
          select: { id: true, name: true, category: true, icon: true },
        },
      },
    });

    if (!tradingSystem) {
      return NextResponse.json(
        { data: null, error: 'Trading system not found' },
        { status: 404 },
      );
    }

    return NextResponse.json({ data: tradingSystem });
  } catch (error) {
    console.error('Error getting trading system:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get trading system' },
      { status: 500 },
    );
  }
}

/**
 * PATCH /api/trading-systems/[id]
 * Update a trading system (partial update).
 */
export async function PATCH(
  request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;
    const body = await request.json();

    // Check if system exists
    const existing = await db.tradingSystem.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { data: null, error: 'Trading system not found' },
        { status: 404 },
      );
    }

    // Build update data from allowed fields
    const allowedFields = [
      'name', 'description', 'category', 'icon',
      'assetFilter', 'phaseConfig', 'entrySignal', 'executionConfig',
      'exitSignal', 'bigDataContext', 'primaryTimeframe', 'confirmTimeframes',
      'maxPositionPct', 'maxOpenPositions', 'stopLossPct', 'takeProfitPct',
      'trailingStopPct', 'cashReservePct', 'allocationMethod', 'allocationConfig',
      'isActive', 'isPaperTrading', 'autoOptimize', 'optimizationMethod', 'optimizationFreq',
      'totalBacktests', 'bestSharpe', 'bestWinRate', 'bestPnlPct', 'avgHoldTimeMin',
    ];

    const updateData: Record<string, unknown> = {};
    for (const field of allowedFields) {
      if (body[field] !== undefined) {
        // JSON-serialize object fields
        const jsonFields = [
          'assetFilter', 'phaseConfig', 'entrySignal', 'executionConfig',
          'exitSignal', 'bigDataContext', 'confirmTimeframes', 'allocationConfig',
        ];
        if (jsonFields.includes(field) && typeof body[field] !== 'string') {
          updateData[field] = JSON.stringify(body[field]);
        } else {
          updateData[field] = body[field];
        }
      }
    }

    // Increment version on config changes
    const configFields = [
      'assetFilter', 'phaseConfig', 'entrySignal', 'executionConfig',
      'exitSignal', 'bigDataContext', 'stopLossPct', 'takeProfitPct',
      'allocationMethod', 'allocationConfig',
    ];
    const hasConfigChange = configFields.some((f) => body[f] !== undefined);
    if (hasConfigChange) {
      updateData.version = existing.version + 1;
    }

    const updated = await db.tradingSystem.update({
      where: { id },
      data: updateData,
    });

    return NextResponse.json({ data: updated });
  } catch (error) {
    console.error('Error updating trading system:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to update trading system' },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/trading-systems/[id]
 * Delete a trading system and all its related data.
 */
export async function DELETE(
  _request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;

    // Check if system exists
    const existing = await db.tradingSystem.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { data: null, error: 'Trading system not found' },
        { status: 404 },
      );
    }

    // Check if other systems derive from this one
    const derivedCount = await db.tradingSystem.count({
      where: { parentSystemId: id },
    });

    if (derivedCount > 0) {
      return NextResponse.json(
        { data: null, error: `Cannot delete: ${derivedCount} derived systems depend on this system. Unlink them first.` },
        { status: 409 },
      );
    }

    // Delete in order: operations → backtests → system
    await db.backtestOperation.deleteMany({ where: { systemId: id } });
    await db.backtestRun.deleteMany({ where: { systemId: id } });
    await db.tradingSystem.delete({ where: { id } });

    return NextResponse.json({ data: { id, deleted: true } });
  } catch (error) {
    console.error('Error deleting trading system:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to delete trading system' },
      { status: 500 },
    );
  }
}
