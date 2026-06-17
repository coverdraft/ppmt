import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/paper-trading/trades
 * Obtener historial de trades desde DB (persistente cross-session).
 */
export async function GET() {
  try {
    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');
    const { db } = await import('@/lib/db');
    
    // Obtener trades desde DB para persistencia
    let dbTrades: Array<{
      id: string;
      positionId: string;
      tokenSymbol: string;
      chain: string;
      direction: string;
      entryPrice: number;
      exitPrice: number;
      quantity: number;
      sizeUsd: number;
      pnlUsd: number;
      pnlPct: number;
      holdTimeMin: number | null;
      mfe: number;
      mae: number;
      exitReason: string | null;
      strategyName: string | null;
      openedAt: Date;
      closedAt: Date;
    }> = [];

    try {
      dbTrades = await db.paperTradingTrade.findMany({
        orderBy: { closedAt: 'desc' },
        take: 200,
        select: {
          id: true,
          positionId: true,
          tokenSymbol: true,
          chain: true,
          direction: true,
          entryPrice: true,
          exitPrice: true,
          quantity: true,
          sizeUsd: true,
          pnlUsd: true,
          pnlPct: true,
          holdTimeMin: true,
          mfe: true,
          mae: true,
          exitReason: true,
          strategyName: true,
          openedAt: true,
          closedAt: true,
        },
      });
    } catch (err) {
      console.warn('Error obteniendo trades de DB, fallback a memoria:', err);
    }

    // Si hay trades en DB, usar esos (persistencia cross-session)
    if (dbTrades.length > 0) {
      return NextResponse.json({
        data: dbTrades.map(t => ({
          id: t.id,
          tokenAddress: '',
          symbol: t.tokenSymbol,
          chain: t.chain,
          direction: t.direction as 'LONG' | 'SHORT',
          entryTime: t.openedAt.toISOString(),
          exitTime: t.closedAt.toISOString(),
          entryPrice: t.entryPrice,
          exitPrice: t.exitPrice,
          positionSizeUsd: t.sizeUsd,
          pnl: t.pnlUsd,
          pnlPct: t.pnlPct,
          holdTimeMin: t.holdTimeMin || 0,
          mfe: t.mfe,
          mae: t.mae,
          exitReason: t.exitReason || '',
          systemName: t.strategyName || '',
        })),
        total: dbTrades.length,
      });
    }

    // Fallback: usar memoria (compatible con versión anterior)
    const memTrades = paperTradingEngine.getTradeHistory();
    return NextResponse.json({
      data: memTrades.map(t => ({
        id: t.id,
        tokenAddress: t.position.tokenAddress,
        symbol: t.position.symbol,
        chain: t.position.chain,
        direction: t.position.direction,
        entryTime: t.position.entryTime.toISOString(),
        exitTime: t.exitTime.toISOString(),
        entryPrice: t.position.entryPrice,
        exitPrice: t.exitPrice,
        positionSizeUsd: t.position.positionSizeUsd,
        pnl: t.pnl,
        pnlPct: t.pnlPct,
        holdTimeMin: t.holdTimeMin,
        mfe: t.mfe,
        mae: t.mae,
        exitReason: t.exitReason,
        systemName: t.position.systemName,
      })),
      total: memTrades.length,
    });
  } catch (error) {
    console.error('Error obteniendo trades de paper trading:', error);
    return NextResponse.json(
      { data: null, error: 'Error al obtener historial de trades' },
      { status: 500 },
    );
  }
}
