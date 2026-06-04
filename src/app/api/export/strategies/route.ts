import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/export/strategies
 * Export trading systems as JSON
 * Query: ?ids=id1,id2 — specific strategies, or all if no ids
 * Query: ?includeBacktests=true — include associated backtest results
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const idsParam = searchParams.get('ids');
    const includeBacktests = searchParams.get('includeBacktests') === 'true';

    const where = idsParam
      ? { id: { in: idsParam.split(',').filter(Boolean) } }
      : {};

    const systems = await db.tradingSystem.findMany({
      where,
      include: includeBacktests
        ? {
            backtests: {
              include: {
                operations: true,
              },
            },
          }
        : {},
      orderBy: { createdAt: 'desc' },
    });

    // Serialize dates to ISO strings
    const exportData = systems.map((system) => ({
      ...system,
      createdAt: system.createdAt.toISOString(),
      updatedAt: system.updatedAt.toISOString(),
      backtests: includeBacktests
        ? (system.backtests as unknown as Array<{ createdAt: Date; completedAt?: Date | null; startedAt?: Date | null; periodStart: Date; periodEnd: Date; operations?: Array<{ entryTime: Date; exitTime?: Date | null; createdAt: Date }> }>).map((bt) => ({
            ...bt,
            periodStart: bt.periodStart.toISOString(),
            periodEnd: bt.periodEnd.toISOString(),
            startedAt: bt.startedAt?.toISOString() ?? null,
            completedAt: bt.completedAt?.toISOString() ?? null,
            createdAt: bt.createdAt.toISOString(),
            operations: bt.operations
              ? bt.operations.map((op) => ({
                  ...op,
                  entryTime: op.entryTime.toISOString(),
                  exitTime: op.exitTime?.toISOString() ?? null,
                  createdAt: op.createdAt.toISOString(),
                }))
              : [],
          }))
        : [],
    }));

    const date = new Date().toISOString().split('T')[0];
    const body = JSON.stringify(
      {
        version: '1.0',
        exportedAt: new Date().toISOString(),
        type: 'strategies',
        data: exportData,
      },
      null,
      2,
    );

    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="cryptoquant-strategies-${date}.json"`,
      },
    });
  } catch (error) {
    console.error('[API /export/strategies] Error:', error);
    return NextResponse.json(
      { error: 'Failed to export strategies' },
      { status: 500 },
    );
  }
}
