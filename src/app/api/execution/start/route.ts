import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// POST /api/execution/start
// Create and execute a paper trade using the autonomous execution engine.
//
// This endpoint bridges the AI Strategy Optimizer's "Activate" flow
// with actual trade execution. After a strategy is activated, calling
// this endpoint will:
//   1. Build a StrategySelection from the TradingSystem config
//   2. Build a PipelineResult from the token data
//   3. Pass both to the autonomous execution engine for paper execution
//   4. Record the trade in BacktestOperation with backtestId = "paper_trading_autonomous"
//
// Body params:
//   systemId: string          - Trading system ID (already activated)
//   tokenAddress?: string     - Token to trade (optional: auto-selects best from backtests)
//   direction?: 'LONG' | 'SHORT' - Trade direction (default: LONG)
//   positionSizeUsd?: number  - Position size in USD (default: from system config or 100)
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { systemId, tokenAddress, direction, positionSizeUsd } = body as {
      systemId: string;
      tokenAddress?: string;
      direction?: 'LONG' | 'SHORT';
      positionSizeUsd?: number;
    };

    // Validate required fields
    if (!systemId) {
      return NextResponse.json(
        { data: null, error: 'systemId is required' },
        { status: 400 },
      );
    }

    // Fetch the trading system
    const system = await db.tradingSystem.findUnique({ where: { id: systemId } });
    if (!system) {
      return NextResponse.json(
        { data: null, error: `Trading system not found: ${systemId}` },
        { status: 404 },
      );
    }

    if (!system.isActive) {
      return NextResponse.json(
        { data: null, error: 'Trading system must be activated before executing trades' },
        { status: 400 },
      );
    }

    // Resolve token to trade: use provided tokenAddress or find best from backtest operations
    let resolvedTokenAddress = tokenAddress || '';
    let resolvedTokenSymbol = '';
    let resolvedEntryPrice = 0;

    if (resolvedTokenAddress) {
      // Use the provided token
      const token = await db.token.findFirst({
        where: { address: resolvedTokenAddress },
        select: { symbol: true, priceUsd: true },
      });
      resolvedTokenSymbol = token?.symbol || resolvedTokenAddress.slice(0, 8);
      resolvedEntryPrice = token?.priceUsd || 0.001;
    } else {
      // Auto-select: find the best-performing token from the system's backtest operations
      const bestOp = await db.backtestOperation.findFirst({
        where: {
          systemId,
          exitPrice: { not: null },
          pnlUsd: { not: null },
        },
        orderBy: { pnlUsd: 'desc' },
      });

      if (bestOp) {
        resolvedTokenAddress = bestOp.tokenAddress;
        resolvedTokenSymbol = bestOp.tokenSymbol || bestOp.tokenAddress.slice(0, 8);

        const token = await db.token.findFirst({
          where: { address: resolvedTokenAddress },
          select: { priceUsd: true },
        });
        resolvedEntryPrice = token?.priceUsd || 0.001;
      } else {
        // Fallback: try any recent operation
        const anyOp = await db.backtestOperation.findFirst({
          where: { systemId },
          orderBy: { entryTime: 'desc' },
        });

        if (anyOp) {
          resolvedTokenAddress = anyOp.tokenAddress;
          resolvedTokenSymbol = anyOp.tokenSymbol || anyOp.tokenAddress.slice(0, 8);

          const token = await db.token.findFirst({
            where: { address: resolvedTokenAddress },
            select: { priceUsd: true },
          });
          resolvedEntryPrice = token?.priceUsd || 0.001;
        } else {
          // Last resort: find any token in the DB
          const anyToken = await db.token.findFirst({
            where: { priceUsd: { gt: 0 } },
            orderBy: { volume24h: 'desc' },
            select: { address: true, symbol: true, priceUsd: true },
          });

          if (anyToken) {
            resolvedTokenAddress = anyToken.address;
            resolvedTokenSymbol = anyToken.symbol;
            resolvedEntryPrice = anyToken.priceUsd;
          } else {
            return NextResponse.json(
              { data: null, error: 'No tokens available for trading. Seed the database first.' },
              { status: 400 },
            );
          }
        }
      }
    }

    if (resolvedEntryPrice <= 0) {
      resolvedEntryPrice = 0.001;
    }

    const resolvedDirection = direction || 'LONG';
    const resolvedPositionSize = positionSizeUsd || Math.min(system.maxPositionPct * 10, 100);

    // Resolve chain from token — infer from address format if not available
    // Use canonical short codes: SOL, ETH, BSC, etc.
    let resolvedChain = 'SOL';
    const tokenForChain = await db.token.findFirst({
      where: { address: resolvedTokenAddress },
      select: { chain: true },
    });
    if (tokenForChain?.chain) {
      resolvedChain = tokenForChain.chain;
    } else if (resolvedTokenAddress.startsWith('0x')) {
      resolvedChain = 'ETH';
    }

    // Parse exit config from system
    let exitConfig: Record<string, unknown> = {};
    try { exitConfig = JSON.parse(system.exitSignal || '{}'); } catch { /* ignore */ }

    const stopLossPct = (exitConfig.stopLoss as number) || system.stopLossPct || 15;
    const takeProfitPct = (exitConfig.takeProfit as number) || system.takeProfitPct || 40;
    const trailingStopPct = (exitConfig.trailingStopPercent as number) || system.trailingStopPct || 25;

    console.log(
      `[ExecStart] Executing paper trade: system=${system.name} ` +
      `token=${resolvedTokenSymbol} dir=${resolvedDirection} ` +
      `size=$${resolvedPositionSize} price=$${resolvedEntryPrice}`
    );

    // Use the strategy evolution engine for reliable paper trade execution
    // This creates proper BacktestRun + BacktestOperation records
    const { strategyEvolutionEngine } = await import('@/lib/services/strategy/strategy-evolution-engine');

    const result = await strategyEvolutionEngine.executeEntry({
      systemId,
      tokenAddress: resolvedTokenAddress,
      tokenSymbol: resolvedTokenSymbol,
      direction: resolvedDirection,
      entryPrice: resolvedEntryPrice,
      positionSizeUsd: resolvedPositionSize,
      chain: resolvedChain,
    });

    // Also create a tracking record in the autonomous backtest for monitoring
    await ensureAutonomousBacktestRun(systemId, resolvedPositionSize);

    await db.backtestOperation.create({
      data: {
        backtestId: AUTONOMOUS_BACKTEST_ID,
        systemId,
        tokenAddress: resolvedTokenAddress,
        tokenSymbol: resolvedTokenSymbol,
        chain: resolvedChain,
        tokenPhase: 'GROWTH',
        tokenAgeMinutes: 0,
        marketConditions: JSON.stringify({ paperTrade: true, source: 'autonomous_execution', tradeId: result.tradeId }),
        tokenDnaSnapshot: JSON.stringify({ systemName: system.name, category: system.category }),
        traderComposition: JSON.stringify({ direction: resolvedDirection }),
        bigDataContext: JSON.stringify({ stopLossPct, takeProfitPct, trailingStopPct }),
        operationType: resolvedDirection,
        timeframe: system.primaryTimeframe,
        entryPrice: resolvedEntryPrice,
        entryTime: new Date(),
        entryReason: JSON.stringify({
          reason: 'autonomous_execution',
          systemName: system.name,
          tradeId: result.tradeId,
          triggeredBy: 'ai_strategy_optimizer',
        }),
        exitPrice: null,
        exitTime: null,
        exitReason: null,
        quantity: resolvedPositionSize / resolvedEntryPrice,
        positionSizeUsd: resolvedPositionSize,
        pnlUsd: null,
        pnlPct: null,
        holdTimeMin: null,
        maxFavorableExc: null,
        maxAdverseExc: null,
        capitalAllocPct: 100,
        allocationMethodUsed: system.allocationMethod,
      },
    });

    // Record state transition
    try {
      const { strategyStateManager } = await import('@/lib/services/strategy/strategy-state-manager');
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
          event: 'execution_start',
          tradeId: result.tradeId,
          tokenAddress: resolvedTokenAddress,
          tokenSymbol: resolvedTokenSymbol,
          direction: resolvedDirection,
          entryPrice: resolvedEntryPrice,
          positionSizeUsd: resolvedPositionSize,
        },
      });
    } catch (stateErr) {
      // State transition recording is best-effort
      console.warn('[ExecStart] Failed to record state transition:', stateErr);
    }

    return NextResponse.json({
      data: {
        executed: true,
        orderId: result.tradeId,
        systemId,
        tokenAddress: resolvedTokenAddress,
        tokenSymbol: resolvedTokenSymbol,
        direction: resolvedDirection,
        entryPrice: resolvedEntryPrice,
        positionSizeUsd: resolvedPositionSize,
        mode: 'PAPER',
        message: `Paper trade executed: ${resolvedDirection} ${resolvedTokenSymbol} at $${resolvedEntryPrice.toFixed(6)}`,
      },
    });
  } catch (error) {
    console.error('[ExecStart] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Execution start failed' },
      { status: 500 },
    );
  }
}

// ============================================================
// Special backtest ID for autonomous execution tracking
// ============================================================

const AUTONOMOUS_BACKTEST_ID = 'paper_trading_autonomous';

/**
 * Ensure the special autonomous backtest run record exists.
 * This is a singleton record used to group all autonomous paper trades.
 */
async function ensureAutonomousBacktestRun(systemId: string, positionSizeUsd: number): Promise<void> {
  const existing = await db.backtestRun.findUnique({
    where: { id: AUTONOMOUS_BACKTEST_ID },
  });

  if (!existing) {
    try {
      await db.backtestRun.create({
        data: {
          id: AUTONOMOUS_BACKTEST_ID,
          systemId,
          mode: 'PAPER',
          periodStart: new Date(),
          periodEnd: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000), // 1 year
          initialCapital: positionSizeUsd,
          allocationMethod: 'KELLY_MODIFIED',
          capitalAllocation: JSON.stringify({ type: 'autonomous_paper_trading' }),
          status: 'RUNNING',
          progress: 0,
          startedAt: new Date(),
        },
      });
    } catch {
      // May have been created concurrently; ignore
    }
  }
}
