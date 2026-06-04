import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/export/full
 * Full export: strategies + trades + config in one JSON
 */
export async function GET() {
  try {
    // Fetch all data in parallel
    const [systems, sessions, positions, trades, alertRules, webhookConfigs] =
      await Promise.all([
        db.tradingSystem.findMany({
          include: {
            backtests: {
              include: { operations: true },
            },
          },
          orderBy: { createdAt: 'desc' },
        }),
        db.paperTradingSession.findMany({ orderBy: { createdAt: 'desc' } }),
        db.paperTradingPosition.findMany({ orderBy: { openedAt: 'desc' } }),
        db.paperTradingTrade.findMany({ orderBy: { closedAt: 'desc' } }),
        db.alertRule.findMany({ orderBy: { createdAt: 'desc' } }),
        db.webhookConfig.findMany({ orderBy: { createdAt: 'desc' } }),
      ]);

    const exportData = {
      version: '1.0',
      exportedAt: new Date().toISOString(),
      type: 'full',
      strategies: systems.map((s) => ({
        ...s,
        createdAt: s.createdAt.toISOString(),
        updatedAt: s.updatedAt.toISOString(),
        backtests: (s.backtests as unknown as Array<{ createdAt: Date; completedAt?: Date | null; startedAt?: Date | null; periodStart: Date; periodEnd: Date; operations?: Array<{ entryTime: Date; exitTime?: Date | null; createdAt: Date }> }>).map((bt) => ({
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
        })),
      })),
      sessions: sessions.map((s) => ({
        ...s,
        startedAt: s.startedAt?.toISOString() ?? null,
        lastScanAt: s.lastScanAt?.toISOString() ?? null,
        lastPriceSyncAt: s.lastPriceSyncAt?.toISOString() ?? null,
        createdAt: s.createdAt.toISOString(),
        updatedAt: s.updatedAt.toISOString(),
      })),
      positions: positions.map((p) => ({
        ...p,
        openedAt: p.openedAt.toISOString(),
        closedAt: p.closedAt?.toISOString() ?? null,
      })),
      trades: trades.map((t) => ({
        ...t,
        openedAt: t.openedAt.toISOString(),
        closedAt: t.closedAt.toISOString(),
      })),
      alertRules: alertRules.map((r) => ({
        ...r,
        lastTriggeredAt: r.lastTriggeredAt?.toISOString() ?? null,
        createdAt: r.createdAt.toISOString(),
        updatedAt: r.updatedAt.toISOString(),
      })),
      webhookConfigs: webhookConfigs.map((w) => ({
        ...w,
        lastDeliveryAt: w.lastDeliveryAt?.toISOString() ?? null,
        createdAt: w.createdAt.toISOString(),
        updatedAt: w.updatedAt.toISOString(),
      })),
    };

    const date = new Date().toISOString().split('T')[0];
    const body = JSON.stringify(exportData, null, 2);

    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="cryptoquant-export-${date}.json"`,
      },
    });
  } catch (error) {
    console.error('[API /export/full] Error:', error);
    return NextResponse.json(
      { error: 'Failed to export full data' },
      { status: 500 },
    );
  }
}
