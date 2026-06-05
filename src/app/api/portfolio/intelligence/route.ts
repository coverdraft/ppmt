import { NextRequest, NextResponse } from 'next/server';
import { portfolioIntelligenceEngine } from '@/lib/services/portfolio/portfolio-intelligence-engine';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/portfolio/intelligence
 * Return current portfolio risk metrics (VaR, diversification, concentration).
 */
export async function GET() {
  try {
    const { db } = await import('@/lib/db');
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    const openPositions = session
      ? await db.paperTradingPosition.findMany({
          where: { status: 'OPEN', runId: session.id },
        })
      : [];

    if (openPositions.length === 0) {
      return NextResponse.json({
        data: {
          portfolioVolatility: 0,
          var95: 0,
          var99: 0,
          historicalVar95: null,
          cvar95: 0,
          diversificationRatio: 0,
          hhi: 0,
          maxDrawdownEstimate: 0,
          timeHorizonDays: 1,
          computedAt: new Date().toISOString(),
        },
      });
    }

    const positions = openPositions.map(p => ({
      id: p.id,
      tokenAddress: p.tokenAddress || '',
      symbol: p.tokenSymbol,
      chain: p.chain,
      sector: killSwitchService.inferSector(p.tokenSymbol, p.chain),
      sizeUsd: p.sizeUsd,
      entryPrice: p.entryPrice,
      currentPrice: p.currentPrice,
      unrealizedPnl: p.pnlUsd,
      unrealizedPnlPct: p.pnlPct,
      weight: p.sizeUsd / (session?.currentCapital ?? 10),
      volatility: 0.6,
      returns: [],
      marketCapTier: 'MID' as const,
      strategyId: p.strategyName || null,
      openedAt: p.openedAt,
    }));

    const metrics = portfolioIntelligenceEngine.computePortfolioRiskMetrics(positions);

    return NextResponse.json({
      data: {
        portfolioVolatility: metrics.portfolioVolatility,
        var95: metrics.var95,
        var99: metrics.var99,
        historicalVar95: metrics.historicalVar95,
        cvar95: metrics.cvar95,
        diversificationRatio: metrics.diversificationRatio,
        hhi: metrics.hhi,
        maxDrawdownEstimate: metrics.maxDrawdownEstimate,
        timeHorizonDays: metrics.timeHorizonDays,
        computedAt: metrics.computedAt.toISOString(),
      },
    });
  } catch (error) {
    console.error('Error getting portfolio intelligence:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to compute portfolio risk metrics' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/portfolio/intelligence
 * Evaluate a proposed position.
 * Body: { tokenAddress, chain, direction, sizeUsd, expectedReturn, expectedVol }
 * Response: { approved, impactScore, riskContribution, diversificationDelta, recommendations }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { tokenAddress, chain, direction, sizeUsd, expectedReturn, expectedVol } = body;

    if (!tokenAddress || !chain || !direction || sizeUsd == null || expectedReturn == null || expectedVol == null) {
      return NextResponse.json(
        { data: null, error: 'Missing required fields: tokenAddress, chain, direction, sizeUsd, expectedReturn, expectedVol' },
        { status: 400 }
      );
    }

    const { db } = await import('@/lib/db');
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    const openPositions = session
      ? await db.paperTradingPosition.findMany({
          where: { status: 'OPEN', runId: session.id },
        })
      : [];

    const currentPositions = openPositions.map(p => ({
      id: p.id,
      tokenAddress: p.tokenAddress || '',
      symbol: p.tokenSymbol,
      chain: p.chain,
      sector: killSwitchService.inferSector(p.tokenSymbol, p.chain),
      sizeUsd: p.sizeUsd,
      entryPrice: p.entryPrice,
      currentPrice: p.currentPrice,
      unrealizedPnl: p.pnlUsd,
      unrealizedPnlPct: p.pnlPct,
      weight: p.sizeUsd / Math.max(session?.currentCapital ?? 10, 1),
      volatility: 0.6,
      returns: [],
      marketCapTier: 'MID' as const,
      strategyId: p.strategyName || null,
      openedAt: p.openedAt,
    }));

    const totalPortfolioValue = Math.max(session?.currentCapital ?? 10, 1);

    const impact = await portfolioIntelligenceEngine.evaluateNewPosition(
      {
        tokenAddress,
        symbol: tokenAddress.slice(0, 8),
        chain,
        sector: killSwitchService.inferSector(tokenAddress, chain),
        proposedSizeUsd: sizeUsd,
        expectedVolatility: expectedVol,
        expectedReturn,
        marketCapTier: 'MID',
        returns: [],
        strategyId: null,
      },
      currentPositions,
      totalPortfolioValue
    );

    return NextResponse.json({
      data: {
        approved: impact.approved,
        impactScore: impact.impactScore,
        riskContribution: impact.riskContribution,
        diversificationDelta: impact.diversificationDelta,
        recommendations: impact.recommendations,
      },
    });
  } catch (error) {
    console.error('Error evaluating proposed position:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to evaluate proposed position' },
      { status: 500 }
    );
  }
}
