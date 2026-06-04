import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserId, userScope } from '@/lib/services/shared/user-data-filter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/paper-trading
 * Obtener estado y stats del paper trading, incluyendo datos de DB.
 */
export async function GET() {
  try {
    const userId = await getCurrentUserId();

    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');
    const { db } = await import('@/lib/db');
    
    const stats = paperTradingEngine.getStatus();
    const openPositions = paperTradingEngine.getOpenPositions();
    
    // Obtener trades desde DB para persistencia cross-session
    let dbTrades: Array<{
      id: string;
      tokenSymbol: string;
      chain: string;
      direction: string;
      entryPrice: number;
      exitPrice: number;
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
      positionId: string;
    }> = [];
    
    try {
      dbTrades = await db.paperTradingTrade.findMany({
        where: { position: userScope(userId) },
        orderBy: { closedAt: 'desc' },
        take: 100,
        select: {
          id: true,
          tokenSymbol: true,
          chain: true,
          direction: true,
          entryPrice: true,
          exitPrice: true,
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
          positionId: true,
        },
      });
    } catch (err) {
      console.warn('Error obteniendo trades de DB:', err);
    }

    // Obtener sesión actual desde DB
    let sessionData: {
      id: string;
      status: string;
      lastPriceSyncAt: Date | null;
    } | null = null;
    try {
      const session = await db.paperTradingSession.findFirst({
        where: userScope(userId),
        orderBy: { createdAt: 'desc' },
      });
      if (session) {
        sessionData = {
          id: session.id,
          status: session.status,
          lastPriceSyncAt: session.lastPriceSyncAt,
        };
      }
    } catch {}

    const recentTrades = openPositions.length > 0 
      ? paperTradingEngine.getTradeHistory().slice(-10)
      : dbTrades.slice(0, 10).map(t => ({
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
        }));

    return NextResponse.json({
      data: {
        stats: {
          ...stats,
          lastPriceSyncAt: stats.lastPriceSyncAt?.toISOString() || null,
          startedAt: stats.startedAt?.toISOString() || null,
          lastScanAt: stats.lastScanAt?.toISOString() || null,
        },
        openPositions: openPositions.map(p => ({
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
        recentTrades,
        session: sessionData ? {
          id: sessionData.id,
          status: sessionData.status,
          lastPriceSyncAt: sessionData.lastPriceSyncAt?.toISOString() || null,
        } : null,
        totalDbTrades: dbTrades.length,
      },
    });
  } catch (error) {
    console.error('Error obteniendo estado de paper trading:', error);
    return NextResponse.json(
      { data: null, error: 'Error al obtener estado de paper trading' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/paper-trading
 * Acciones: start, stop, pause, resume, scan, sync_prices, activate_strategy
 */
export async function POST(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');
    const body = await request.json();
    const { action, config, ...params } = body as {
      action?: string;
      config?: Record<string, unknown>;
      [key: string]: unknown;
    };

    switch (action) {
      case 'start': {
        const result = await paperTradingEngine.start(config || {});
        return NextResponse.json({ data: result });
      }

      case 'stop': {
        const result = await paperTradingEngine.stop();
        return NextResponse.json({ data: result });
      }

      case 'pause': {
        await paperTradingEngine.pause();
        return NextResponse.json({ data: { paused: true } });
      }

      case 'resume': {
        await paperTradingEngine.resume();
        return NextResponse.json({ data: { resumed: true } });
      }

      case 'scan': {
        const result = await paperTradingEngine.runSingleScan();
        return NextResponse.json({ data: result });
      }

      case 'sync_prices': {
        const result = await paperTradingEngine.syncOpenPositionPrices();
        return NextResponse.json({ 
          data: { 
            synced: true, 
            updated: result.updated, 
            errors: result.errors,
            timestamp: new Date().toISOString(),
          } 
        });
      }

      case 'activate_strategy': {
        const { tokenAddress, tokenSymbol, chain, strategyName, direction, operabilityScore } = params as {
          tokenAddress?: string;
          tokenSymbol?: string;
          chain?: string;
          strategyName?: string;
          direction?: string;
          operabilityScore?: number;
        };

        if (!tokenAddress || !tokenSymbol || !chain) {
          return NextResponse.json(
            { data: null, error: 'tokenAddress, tokenSymbol y chain son requeridos' },
            { status: 400 },
          );
        }

        const result = await paperTradingEngine.activateStrategy({
          tokenAddress,
          tokenSymbol,
          chain,
          strategyName: strategyName || 'AI Manager',
          direction: (direction as 'LONG' | 'SHORT') || 'LONG',
          operabilityScore,
        });

        return NextResponse.json({ data: result });
      }

      default:
        return NextResponse.json(
          { data: null, error: 'Acción inválida. Usar: start, stop, pause, resume, scan, sync_prices, activate_strategy' },
          { status: 400 },
        );
    }
  } catch (error) {
    console.error('Error en acción de paper trading:', error);
    return NextResponse.json(
      { data: null, error: 'Error al ejecutar acción de paper trading' },
      { status: 500 },
    );
  }
}
