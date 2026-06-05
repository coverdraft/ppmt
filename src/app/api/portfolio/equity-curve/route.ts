import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/portfolio/equity-curve?from=ISO&to=ISO
 *
 * Returns portfolio equity curve data:
 * - Array of { date, portfolioValue, benchmarkValue, events[] }
 * - Computed from PaperTradingSession + PaperTradingTrade history
 * - Events: kill switch triggers, SDE decisions, large trades
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const from = searchParams.get('from') ? new Date(searchParams.get('from')!) : undefined;
    const to = searchParams.get('to') ? new Date(searchParams.get('to')!) : undefined;

    // Get the most recent session
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    if (!session) {
      return NextResponse.json({
        data: {
          curve: [],
          initialCapital: 0,
          currentCapital: 0,
        },
      });
    }

    const initialCapital = session.initialCapital;

    // Get all closed trades ordered by time
    const where: Record<string, unknown> = {};
    if (from || to) {
      where.closedAt = {};
      if (from) (where.closedAt as Record<string, unknown>).gte = from;
      if (to) (where.closedAt as Record<string, unknown>).lte = to;
    }

    const trades = await db.paperTradingTrade.findMany({
      where: {
        ...where,
        position: { runId: session.id },
      },
      orderBy: { closedAt: 'asc' },
      select: {
        id: true,
        tokenSymbol: true,
        pnlUsd: true,
        pnlPct: true,
        sizeUsd: true,
        exitReason: true,
        closedAt: true,
        openedAt: true,
      },
    });

    // Get open positions for current valuation
    const openPositions = await db.paperTradingPosition.findMany({
      where: { status: 'OPEN', runId: session.id },
      select: {
        id: true,
        tokenSymbol: true,
        sizeUsd: true,
        pnlUsd: true,
        pnlPct: true,
        openedAt: true,
      },
    });

    // Build equity curve from trades
    let runningCapital = initialCapital;
    const curve: Array<{
      date: string;
      portfolioValue: number;
      benchmarkValue: number;
      events: Array<{ type: string; description: string }>;
    }> = [];

    // Starting point
    curve.push({
      date: session.startedAt?.toISOString() ?? session.createdAt.toISOString(),
      portfolioValue: initialCapital,
      benchmarkValue: initialCapital,
      events: [{ type: 'SESSION_START', description: `Paper trading session started with $${initialCapital}` }],
    });

    // Process each trade
    for (const trade of trades) {
      runningCapital += trade.pnlUsd;

      const events: Array<{ type: string; description: string }> = [];

      // Detect large trades (PnL > 5% of running capital)
      if (Math.abs(trade.pnlUsd) > runningCapital * 0.05) {
        events.push({
          type: 'LARGE_TRADE',
          description: `${trade.tokenSymbol}: ${trade.pnlUsd >= 0 ? '+' : ''}$${trade.pnlUsd.toFixed(2)} (${trade.exitReason || 'exit'})`,
        });
      }

      // Detect kill switch triggered exits
      if (trade.exitReason?.includes('KILL_SWITCH')) {
        events.push({
          type: 'KILL_SWITCH',
          description: `Kill switch triggered: ${trade.tokenSymbol} — ${trade.exitReason}`,
        });
      }

      // Detect stop loss exits
      if (trade.exitReason?.includes('STOP_LOSS')) {
        events.push({
          type: 'STOP_LOSS',
          description: `Stop loss: ${trade.tokenSymbol} @ ${trade.pnlPct >= 0 ? '+' : ''}${trade.pnlPct.toFixed(1)}%`,
        });
      }

      curve.push({
        date: trade.closedAt.toISOString(),
        portfolioValue: Math.round(runningCapital * 100) / 100,
        benchmarkValue: initialCapital, // Benchmark = initial capital (buy & hold cash)
        events,
      });
    }

    // Add current valuation with open positions
    const unrealizedPnl = openPositions.reduce((sum, p) => sum + p.pnlUsd, 0);
    const currentValue = runningCapital + unrealizedPnl;

    if (openPositions.length > 0) {
      curve.push({
        date: new Date().toISOString(),
        portfolioValue: Math.round(currentValue * 100) / 100,
        benchmarkValue: initialCapital,
        events: openPositions.map(p => ({
          type: 'OPEN_POSITION',
          description: `${p.tokenSymbol}: unrealized ${p.pnlPct >= 0 ? '+' : ''}${p.pnlPct.toFixed(1)}%`,
        })),
      });
    }

    // Get SDE decision audit events for overlay
    const audits = await db.decisionAudit.findMany({
      where: {
        timestamp: {
          gte: session.startedAt ?? session.createdAt,
        },
      },
      orderBy: { timestamp: 'asc' },
      select: {
        id: true,
        strategyId: true,
        timestamp: true,
        decision: true,
      },
      take: 100,
    });

    // Merge audit events into the curve
    for (const audit of audits) {
      try {
        const decisionData = JSON.parse(audit.decision) as Record<string, unknown>;
        const state = decisionData.state as string;
        const capitalAction = decisionData.capitalAction as string;
        const strategyName = decisionData.strategyName as string;

        // Find the closest curve point after this audit
        const auditTime = new Date(audit.timestamp).getTime();
        let closestIdx = -1;
        let closestDiff = Infinity;
        for (let i = 0; i < curve.length; i++) {
          const curveTime = new Date(curve[i].date).getTime();
          const diff = Math.abs(curveTime - auditTime);
          if (diff < closestDiff) {
            closestDiff = diff;
            closestIdx = i;
          }
        }

        if (closestIdx >= 0 && closestDiff < 24 * 60 * 60 * 1000) {
          curve[closestIdx].events.push({
            type: 'SDE_DECISION',
            description: `${strategyName || audit.strategyId}: ${state} → ${capitalAction}`,
          });
        }
      } catch {
        // Skip malformed audit data
      }
    }

    return NextResponse.json({
      data: {
        curve,
        initialCapital,
        currentCapital: Math.round(currentValue * 100) / 100,
        totalReturnPct: initialCapital > 0
          ? Math.round(((currentValue - initialCapital) / initialCapital) * 10000) / 100
          : 0,
        totalTrades: trades.length,
        openPositions: openPositions.length,
        auditEventCount: audits.length,
      },
    });
  } catch (error) {
    console.error('[Portfolio Equity Curve API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Failed to fetch equity curve' },
      { status: 500 },
    );
  }
}
