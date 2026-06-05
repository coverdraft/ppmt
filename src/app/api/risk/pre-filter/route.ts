import { NextRequest, NextResponse } from 'next/server';
import { riskPreFilter } from '@/lib/services/risk/risk-pre-filter';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/risk/pre-filter
 * Return current filter status and statistics.
 */
export async function GET() {
  try {
    const ksState = killSwitchService.getState();

    return NextResponse.json({
      data: {
        globalPause: ksState.globalPause,
        portfolioDDTriggered: ksState.portfolioDDTriggered,
        strategyPauses: Object.fromEntries(
          Array.from(ksState.strategyPauses.entries()).map(([k, v]) => [
            k,
            { paused: v.paused, reason: v.reason, pausedAt: v.pausedAt.toISOString() },
          ])
        ),
        blacklistedTokens: riskPreFilter.getBlacklistedTokens(),
        lastEvaluatedAt: ksState.lastEvaluatedAt?.toISOString() ?? null,
      },
    });
  } catch (error) {
    console.error('Error getting pre-filter status:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get pre-filter status' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/risk/pre-filter
 * Run pre-filter on a given signal.
 * Body: { tokenAddress, chain, direction, confidence, strategyName, sizeUsd }
 * Response: { passed, rejectionReasons, warnings, riskScore, adjustedConfidence }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { tokenAddress, chain, direction, confidence, strategyName, sizeUsd } = body;

    if (!tokenAddress || !chain || !direction || confidence == null || !strategyName || sizeUsd == null) {
      return NextResponse.json(
        { data: null, error: 'Missing required fields: tokenAddress, chain, direction, confidence, strategyName, sizeUsd' },
        { status: 400 }
      );
    }

    // Build portfolio state from current paper trading positions
    const { db } = await import('@/lib/db');
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    const openPositions = session
      ? await db.paperTradingPosition.findMany({
          where: { status: 'OPEN', runId: session.id },
        })
      : [];

    const totalCapital = session?.currentCapital ?? 10;
    const totalPositionValue = openPositions.reduce((sum, p) => sum + p.sizeUsd, 0);

    const portfolioState = {
      totalCapital,
      freeCapital: totalCapital - totalPositionValue,
      openPositions: openPositions.map(p => ({
        tokenAddress: p.tokenAddress || '',
        chain: p.chain,
        sizeUsd: p.sizeUsd,
        pnlPct: p.pnlPct,
        direction: p.direction as 'LONG' | 'SHORT',
      })),
      currentDD: session
        ? (session.peakCapital > 0
          ? Math.max(0, (session.peakCapital - session.currentCapital) / session.peakCapital)
          : 0)
        : 0,
      dailyPnL: 0,
    };

    const result = await riskPreFilter.filter(
      {
        tokenAddress,
        chain,
        direction,
        confidence,
        strategyName,
        signalType: direction === 'LONG' ? 'MOMENTUM' : 'EXIT',
        sizeUsd,
      },
      portfolioState
    );

    return NextResponse.json({
      data: {
        passed: result.passed,
        rejectionReasons: result.rejectionReasons,
        warnings: result.warnings,
        riskScore: result.riskScore,
        adjustedConfidence: result.adjustedConfidence,
      },
    });
  } catch (error) {
    console.error('Error running pre-filter:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run pre-filter' },
      { status: 500 }
    );
  }
}
