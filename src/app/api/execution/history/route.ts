import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/execution/history
 *
 * Dedicated endpoint for fetching closed trade history.
 * Query params:
 *   - systemId (optional): filter by trading system ID
 *   - limit (optional): max records to return (default 100)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const systemId = searchParams.get('systemId') || undefined;
    const limit = Math.min(Number(searchParams.get('limit')) || 100, 500);

    const { strategyEvolutionEngine } = await import('@/lib/services/strategy/strategy-evolution-engine');
    const history = await strategyEvolutionEngine.getTradeHistory(systemId, limit);

    return NextResponse.json({
      data: history.map(trade => ({
        id: trade.id,
        systemId: trade.systemId,
        systemName: trade.systemName,
        tokenAddress: trade.tokenAddress,
        tokenSymbol: trade.tokenSymbol,
        direction: trade.direction,
        entryPrice: trade.entryPrice,
        exitPrice: trade.exitPrice,
        entryTime: trade.entryTime,
        exitTime: trade.exitTime,
        pnlUsd: trade.pnlUsd,
        pnlPct: trade.pnlPct,
        holdTimeMin: trade.holdTimeMin,
        exitReason: trade.exitReason,
        mode: trade.mode,
      })),
      total: history.length,
    });
  } catch (error) {
    console.error('[Execution History API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Failed to fetch trade history' },
      { status: 500 },
    );
  }
}
