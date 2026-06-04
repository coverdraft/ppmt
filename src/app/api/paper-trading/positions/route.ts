import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/paper-trading/positions
 * Obtener posiciones abiertas desde DB + caché en memoria.
 */
export async function GET() {
  try {
    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');
    const { db } = await import('@/lib/db');
    
    // Primero obtener de caché en memoria
    const memPositions = paperTradingEngine.getOpenPositions();
    
    // Si hay posiciones en memoria, usar esas (más actualizadas)
    if (memPositions.length > 0) {
      return NextResponse.json({
        data: memPositions.map(p => ({
          id: p.id,
          tokenAddress: p.tokenAddress,
          symbol: p.symbol,
          chain: p.chain,
          direction: p.direction,
          entryTime: p.entryTime.toISOString(),
          entryPrice: p.entryPrice,
          currentPrice: p.currentPrice,
          positionSizeUsd: p.positionSizeUsd,
          unrealizedPnl: p.unrealizedPnl,
          unrealizedPnlPct: p.unrealizedPnlPct,
          highWaterMark: p.highWaterMark,
          systemName: p.systemName,
          exitConditions: p.exitConditions,
        })),
      });
    }

    // Fallback: obtener de DB (para restauración después de restart)
    const runId = paperTradingEngine.getCurrentRunId();
    let dbPositions: Array<{
      id: string;
      tokenSymbol: string;
      tokenAddress: string | null;
      chain: string;
      direction: string;
      entryPrice: number;
      currentPrice: number;
      quantity: number;
      sizeUsd: number;
      pnlUsd: number;
      pnlPct: number;
      highestPrice: number;
      strategyName: string | null;
      stopLoss: number | null;
      takeProfit: number | null;
      trailingStopPct: number | null;
      openedAt: Date;
    }> = [];

    if (runId) {
      try {
        dbPositions = await db.paperTradingPosition.findMany({
          where: { status: 'OPEN', runId },
          select: {
            id: true,
            tokenSymbol: true,
            tokenAddress: true,
            chain: true,
            direction: true,
            entryPrice: true,
            currentPrice: true,
            quantity: true,
            sizeUsd: true,
            pnlUsd: true,
            pnlPct: true,
            highestPrice: true,
            strategyName: true,
            stopLoss: true,
            takeProfit: true,
            trailingStopPct: true,
            openedAt: true,
          },
        });
      } catch {}
    }

    return NextResponse.json({
      data: dbPositions.map(p => {
        const exitConditions: string[] = [];
        if (p.stopLoss !== null) exitConditions.push('stop_loss');
        if (p.takeProfit !== null) exitConditions.push('take_profit');
        if (p.trailingStopPct && p.trailingStopPct > 0) exitConditions.push('trailing_stop');
        exitConditions.push('brain_signal_change');

        return {
          id: p.id,
          tokenAddress: p.tokenAddress || '',
          symbol: p.tokenSymbol,
          chain: p.chain,
          direction: p.direction,
          entryTime: p.openedAt.toISOString(),
          entryPrice: p.entryPrice,
          currentPrice: p.currentPrice,
          positionSizeUsd: p.sizeUsd,
          unrealizedPnl: p.pnlUsd,
          unrealizedPnlPct: p.pnlPct,
          highWaterMark: p.highestPrice,
          systemName: p.strategyName || '',
          exitConditions,
        };
      }),
    });
  } catch (error) {
    console.error('Error obteniendo posiciones de paper trading:', error);
    return NextResponse.json(
      { data: null, error: 'Error al obtener posiciones' },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/paper-trading/positions
 * Forzar cierre de una posición de paper trading.
 */
export async function DELETE(request: NextRequest) {
  try {
    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');
    const body = await request.json();
    const { positionId, reason } = body as { positionId?: string; reason?: string };

    if (!positionId) {
      return NextResponse.json(
        { data: null, error: 'positionId es requerido' },
        { status: 400 },
      );
    }

    const closedTrade = await paperTradingEngine.forceClosePosition(
      positionId,
      reason || 'manual_close',
    );

    if (!closedTrade) {
      return NextResponse.json(
        { data: null, error: 'Posición no encontrada' },
        { status: 404 },
      );
    }

    return NextResponse.json({
      data: {
        id: closedTrade.id,
        tokenAddress: closedTrade.position.tokenAddress,
        symbol: closedTrade.position.symbol,
        exitPrice: closedTrade.exitPrice,
        exitReason: closedTrade.exitReason,
        pnl: closedTrade.pnl,
        pnlPct: closedTrade.pnlPct,
        holdTimeMin: closedTrade.holdTimeMin,
      },
    });
  } catch (error) {
    console.error('Error cerrando posición de paper trading:', error);
    return NextResponse.json(
      { data: null, error: 'Error al cerrar posición' },
      { status: 500 },
    );
  }
}
