import { NextRequest, NextResponse } from 'next/server';
import { strategyEvolutionEngine } from '@/lib/services/strategy/strategy-evolution-engine';
import { strategyStateManager } from '@/lib/services/strategy/strategy-state-manager';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// POST /api/execution/auto-exit
// Close an open paper trade position.
//
// Body params:
//   backtestId: string   - The backtest run ID (paper trade container)
//   exitPrice?: number   - Exit price (optional, fetches from token DB if missing)
//   exitReason?: string  - Reason for exit (default: 'auto_exit')
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { backtestId, exitPrice, exitReason } = body as {
      backtestId: string;
      exitPrice?: number;
      exitReason?: string;
    };

    if (!backtestId) {
      return NextResponse.json(
        { data: null, error: 'backtestId is required' },
        { status: 400 },
      );
    }

    // Resolve exit price from the open position's token if not provided
    let resolvedExitPrice = exitPrice || 0;
    let systemId: string | null = null;

    // Find the open operation to get the token address and systemId
    const operation = await db.backtestOperation.findFirst({
      where: { backtestId, exitPrice: null },
      orderBy: { entryTime: 'desc' },
    });

    if (operation) {
      systemId = operation.systemId;

      if (resolvedExitPrice === 0) {
        const token = await db.token.findFirst({
          where: { address: operation.tokenAddress },
          select: { priceUsd: true, symbol: true },
        });
        if (token) {
          resolvedExitPrice = token.priceUsd || operation.entryPrice;
        } else {
          resolvedExitPrice = operation.entryPrice; // Fallback to entry price
        }
      }
    }

    if (resolvedExitPrice === 0) {
      return NextResponse.json(
        { data: null, error: 'Cannot determine exit price. Provide exitPrice or ensure the position has a valid token.' },
        { status: 400 },
      );
    }

    const reason = exitReason || 'auto_exit';

    console.log(
      `[AutoExit] Executing exit: backtest=${backtestId} price=$${resolvedExitPrice} reason=${reason}`
    );

    // Execute the exit via the strategy evolution engine
    const result = await strategyEvolutionEngine.executeExit({
      backtestId,
      exitPrice: resolvedExitPrice,
      exitReason: reason,
    });

    // Record state transition via strategy-state-manager
    if (systemId) {
      try {
        // Count remaining open positions for this system
        const remainingOpenPositions = await db.backtestOperation.count({
          where: {
            systemId,
            exitPrice: null,
            backtest: { mode: 'PAPER' },
          },
        });

        // If no more open positions, transition back to IDLE; otherwise stay in PAPER_TRADING
        const newStatus = remainingOpenPositions === 0 ? 'IDLE' : 'PAPER_TRADING';

        await strategyStateManager.recordStateTransition({
          systemId,
          newStatus,
          triggerReason: 'SCHEDULER',
          metrics: {
            totalPnlUsd: result.pnlUsd,
            totalPnlPct: result.pnlPct,
            openPositions: remainingOpenPositions,
          },
          metadata: {
            event: 'auto_exit_executed',
            backtestId,
            exitReason: reason,
            exitPrice: resolvedExitPrice,
            pnlUsd: result.pnlUsd,
            pnlPct: result.pnlPct,
          },
        });
      } catch (stateErr) {
        // State transition recording is best-effort; don't fail the exit
        console.warn('[AutoExit] Failed to record state transition:', stateErr);
      }
    }

    return NextResponse.json({
      data: {
        backtestId,
        exitPrice: resolvedExitPrice,
        exitReason: reason,
        pnlUsd: result.pnlUsd,
        pnlPct: result.pnlPct,
        status: result.status,
        message: `Exit executed: PnL ${result.pnlUsd >= 0 ? '+' : ''}$${result.pnlUsd.toFixed(2)} (${result.pnlPct >= 0 ? '+' : ''}${result.pnlPct.toFixed(2)}%)`,
      },
    });
  } catch (error) {
    console.error('[AutoExit] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Auto-exit execution failed' },
      { status: 500 },
    );
  }
}
