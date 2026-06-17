import { NextRequest, NextResponse } from 'next/server';
import { strategyEvolutionEngine } from '@/lib/services/strategy/strategy-evolution-engine';
import { strategyStateManager } from '@/lib/services/strategy/strategy-state-manager';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// POST /api/execution/auto-trade
// Execute a paper trade entry for a given strategy.
//
// Body params:
//   systemId: string        - Trading system ID to execute under
//   tokenAddress: string    - Token contract address
//   direction: 'LONG' | 'SHORT' - Trade direction
//   positionSizeUsd: number - Position size in USD
//   entryPrice?: number     - Override entry price (optional, defaults to token's current price from DB)
//   tokenSymbol?: string    - Token symbol (optional, fetched from DB if missing)
//   chain?: string          - Chain (default 'ETH')
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { systemId, tokenAddress, direction, positionSizeUsd, entryPrice, tokenSymbol, chain } = body as {
      systemId: string;
      tokenAddress: string;
      direction: 'LONG' | 'SHORT';
      positionSizeUsd: number;
      entryPrice?: number;
      tokenSymbol?: string;
      chain?: string;
    };

    // Validate required fields
    if (!systemId) {
      return NextResponse.json(
        { data: null, error: 'systemId is required' },
        { status: 400 },
      );
    }
    if (!tokenAddress) {
      return NextResponse.json(
        { data: null, error: 'tokenAddress is required' },
        { status: 400 },
      );
    }
    if (!direction || !['LONG', 'SHORT'].includes(direction)) {
      return NextResponse.json(
        { data: null, error: 'direction must be LONG or SHORT' },
        { status: 400 },
      );
    }
    if (!positionSizeUsd || positionSizeUsd <= 0) {
      return NextResponse.json(
        { data: null, error: 'positionSizeUsd must be a positive number' },
        { status: 400 },
      );
    }

    // Resolve token symbol and entry price from DB if not provided
    let resolvedSymbol = tokenSymbol || '';
    let resolvedPrice = entryPrice || 0;

    if (!resolvedSymbol || resolvedPrice === 0) {
      const token = await db.token.findFirst({
        where: { address: tokenAddress },
        select: { symbol: true, priceUsd: true },
      });
      if (token) {
        resolvedSymbol = resolvedSymbol || token.symbol || tokenAddress.slice(0, 8);
        resolvedPrice = resolvedPrice || token.priceUsd || 0.001;
      }
    }

    // Fallback defaults
    if (!resolvedSymbol) resolvedSymbol = tokenAddress.slice(0, 8);
    if (resolvedPrice === 0) resolvedPrice = 0.001; // Minimal fallback

    console.log(
      `[AutoTrade] Executing entry: system=${systemId} token=${resolvedSymbol} ` +
      `dir=${direction} size=$${positionSizeUsd} price=$${resolvedPrice}`
    );

    // Execute the entry via the strategy evolution engine
    const result = await strategyEvolutionEngine.executeEntry({
      systemId,
      tokenAddress,
      tokenSymbol: resolvedSymbol,
      direction,
      entryPrice: resolvedPrice,
      positionSizeUsd,
      chain: chain || 'SOL',
    });

    // Record state transition to PAPER_TRADING via strategy-state-manager
    try {
      // Count current open positions for this system
      const openPositionsCount = await db.backtestOperation.count({
        where: {
          systemId,
          exitPrice: null,
          backtest: { mode: 'PAPER' },
        },
      });

      await strategyStateManager.recordStateTransition({
        systemId,
        newStatus: 'PAPER_TRADING',
        triggerReason: 'SCHEDULER',
        metrics: {
          openPositions: openPositionsCount,
        },
        metadata: {
          event: 'auto_trade_entry',
          tradeId: result.tradeId,
          tokenAddress,
          tokenSymbol: resolvedSymbol,
          direction,
          entryPrice: resolvedPrice,
          positionSizeUsd,
        },
      });
    } catch (stateErr) {
      // State transition recording is best-effort; don't fail the trade
      console.warn('[AutoTrade] Failed to record state transition:', stateErr);
    }

    return NextResponse.json({
      data: {
        tradeId: result.tradeId,
        status: result.status,
        systemId,
        tokenAddress,
        tokenSymbol: resolvedSymbol,
        direction,
        entryPrice: resolvedPrice,
        positionSizeUsd,
        message: `Entry executed: ${direction} ${resolvedSymbol} at $${resolvedPrice.toFixed(6)}`,
      },
    });
  } catch (error) {
    console.error('[AutoTrade] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Auto-trade execution failed' },
      { status: 500 },
    );
  }
}
