import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/export/trades
 * Export paper trading history
 * Query: ?sessionId=xxx — specific session
 * Query: ?format=json|csv — JSON or CSV format
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const sessionId = searchParams.get('sessionId');
    const format = searchParams.get('format') || 'json';

    // Fetch sessions
    const sessionWhere = sessionId ? { id: sessionId } : {};
    const sessions = await db.paperTradingSession.findMany({
      where: sessionWhere,
      orderBy: { createdAt: 'desc' },
    });

    // Fetch positions
    const positionWhere = sessionId ? { runId: sessionId } : {};
    const positions = await db.paperTradingPosition.findMany({
      where: positionWhere,
      orderBy: { openedAt: 'desc' },
    });

    // Fetch trades
    const positionIds = positions.map((p) => p.id);
    const tradeWhere = positionIds.length > 0 ? { positionId: { in: positionIds } } : {};
    const trades = await db.paperTradingTrade.findMany({
      where: tradeWhere,
      orderBy: { closedAt: 'desc' },
    });

    const date = new Date().toISOString().split('T')[0];

    if (format === 'csv') {
      // Generate CSV for trades
      const headers = [
        'id', 'positionId', 'tokenSymbol', 'chain', 'direction',
        'entryPrice', 'exitPrice', 'quantity', 'sizeUsd',
        'pnlUsd', 'pnlPct', 'mfe', 'mae', 'exitReason',
        'strategyName', 'holdTimeMin', 'openedAt', 'closedAt',
      ];

      const csvRows = [
        headers.join(','),
        ...trades.map((t) =>
          headers
            .map((h) => {
              const val = (t as Record<string, unknown>)[h];
              if (val === null || val === undefined) return '';
              if (val instanceof Date) return val.toISOString();
              const str = String(val);
              // Escape CSV values containing commas or quotes
              if (str.includes(',') || str.includes('"') || str.includes('\n')) {
                return `"${str.replace(/"/g, '""')}"`;
              }
              return str;
            })
            .join(','),
        ),
      ].join('\n');

      return new NextResponse(csvRows, {
        status: 200,
        headers: {
          'Content-Type': 'text/csv',
          'Content-Disposition': `attachment; filename="cryptoquant-trades-${date}.csv"`,
        },
      });
    }

    // JSON format
    const exportData = {
      version: '1.0',
      exportedAt: new Date().toISOString(),
      type: 'trades',
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
    };

    const body = JSON.stringify(exportData, null, 2);

    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="cryptoquant-trades-${date}.json"`,
      },
    });
  } catch (error) {
    console.error('[API /export/trades] Error:', error);
    return NextResponse.json(
      { error: 'Failed to export trades' },
      { status: 500 },
    );
  }
}
