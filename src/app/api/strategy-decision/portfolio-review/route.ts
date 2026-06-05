import { NextRequest, NextResponse } from 'next/server';
import {
  strategyDecisionEngine,
  type SDEInput,
  type RiskProfile,
} from '@/lib/services/strategy/strategy-decision-engine';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/strategy-decision/portfolio-review
 *
 * Review all active strategies through the SDE pipeline.
 * Returns sorted decisions (REJECTED first, then PAUSED, CONDITIONAL, ACTIVE).
 *
 * Query params:
 *   riskProfile=MODERATE  (optional)
 *   capital=100           (portfolio capital in USD)
 *   activeOnly=true       (only review active strategies)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const riskProfileRaw = searchParams.get('riskProfile') || 'MODERATE';
    const riskProfile = (['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'].includes(riskProfileRaw) ? riskProfileRaw : 'MODERATE') as RiskProfile;
    const capital = parseFloat(searchParams.get('capital') || '100');
    const activeOnly = searchParams.get('activeOnly') === 'true';
    const skipAudit = searchParams.get('skipAudit') === 'true';

    // Fetch trading systems
    const where: Record<string, unknown> = {};
    if (activeOnly) where.isActive = true;

    const systems = await db.tradingSystem.findMany({
      where,
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { createdAt: 'desc' },
          take: 1,
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    if (systems.length === 0) {
      return NextResponse.json({
        data: {
          decisions: [],
          summary: { total: 0, active: 0, conditional: 0, paused: 0, rejected: 0 },
        },
      });
    }

    // Build inputs for each strategy
    const inputs: SDEInput[] = [];

    for (const system of systems) {
      // Use pre-fetched backtest from include (no N+1 query)
      const backtests = (system as Record<string, unknown>)['backtests'] as Array<Record<string, number | string | Date | null>> | undefined;
      const bt = backtests?.[0];

      // Extract numeric values with type-safe defaults
      const totalTrades = Number(bt?.totalTrades ?? 0);
      const winRate = Number(bt?.winRate ?? 0);
      const avgWin = Number(bt?.avgWin ?? 0);
      const avgLoss = Number(bt?.avgLoss ?? 0);
      const maxDrawdownPct = Number(bt?.maxDrawdownPct ?? 0);
      const sharpeRatio = Number(bt?.sharpeRatio ?? 0);
      const sortinoRatio = Number(bt?.sortinoRatio ?? 0);
      const profitFactor = Number(bt?.profitFactor ?? 0);
      const expectancy = Number(bt?.expectancy ?? 0);
      const inSampleScore = bt?.inSampleScore != null ? Number(bt.inSampleScore) : null;
      const outOfSampleScore = bt?.outOfSampleScore != null ? Number(bt.outOfSampleScore) : null;
      const walkForwardRatio = bt?.walkForwardRatio != null ? Number(bt.walkForwardRatio) : null;
      const recoveryFactor = Number(bt?.recoveryFactor ?? 0);

      const backtest = {
        totalTrades,
        winRate,
        avgWinPct: avgWin,
        avgLossPct: Math.abs(avgLoss),
        maxDrawdownPct,
        sharpeRatio,
        sortinoRatio,
        profitFactor,
        expectancy,
        overfittingScore: inSampleScore != null && outOfSampleScore != null
          ? Math.max(0, inSampleScore - outOfSampleScore)
          : 0.5,
        parameterStability: walkForwardRatio ?? 0.5,
        recoveryFactor,
        payoffRatio: (avgWin && avgLoss && avgLoss !== 0)
          ? avgWin / Math.abs(avgLoss)
          : 1,
      };

      inputs.push({
        strategyId: system.id,
        strategyName: system.name,
        backtest,
        monteCarlo: {
          riskOfRuin: backtest.maxDrawdownPct > 30 ? 0.10 : 0.02,
          probabilityOfProfit: backtest.winRate > 0.5 ? 0.65 : 0.35,
          p95MaxDrawdown: backtest.maxDrawdownPct * 1.5,
          meanFinalEquity: capital,
          medianFinalEquity: capital,
          stdDevFinalEquity: capital * 0.3,
          simulationsCount: 0,
          ruinThreshold: 0.5,
        },
        walkForward: {
          aggregateWFE: backtest.parameterStability,
          isRobust: backtest.parameterStability >= 0.3,
          recommendation: backtest.parameterStability >= 0.5 ? 'ROBUST'
            : backtest.parameterStability >= 0.3 ? 'MARGINAL' : 'OVERFIT',
          parameterStability: backtest.parameterStability,
          overallDegradation: backtest.overfittingScore,
          performanceConsistency: 1 - backtest.overfittingScore,
          windowCount: 0,
        },
        operability: {
          overallScore: 50,
          level: 'MARGINAL',
          isOperable: true,
          recommendedPositionUsd: capital * 0.05,
          minimumGainPct: 3,
          feeEstimateTotalCostPct: 1,
        },
        portfolioState: {
          totalCapitalUsd: capital,
          currentDrawdownPct: 0,
          activeStrategies: systems.length,
          marketVolatility: 50,
          marketRegime: 'SIDEWAYS',
        },
        riskProfile,
        dataQuality: 'PLACEHOLDER', // MC/WF data is fabricated — no real simulation was run
      });

      // Warn when placeholder data is used — SDE only downgrades to CONDITIONAL
      // but decisions based on fabricated MC/WF numbers can be dangerously misleading
      console.warn(
        `[PortfolioReview] Strategy "${system.name}" uses PLACEHOLDER Monte Carlo / Walk-Forward data. ` +
        `SDE decisions for this strategy are based on fabricated statistics, not real simulations.`,
      );
    }

    // Run portfolio review (skip audit creation when auto-refreshing to prevent audit flood)
    const decisions = await strategyDecisionEngine.portfolioReview(inputs, riskProfile, skipAudit);

    // Summary
    const summary = {
      total: decisions.length,
      active: decisions.filter(d => d.state === 'ACTIVE').length,
      conditional: decisions.filter(d => d.state === 'CONDITIONAL').length,
      paused: decisions.filter(d => d.state === 'PAUSED').length,
      rejected: decisions.filter(d => d.state === 'REJECTED').length,
    };

    return NextResponse.json({ data: { decisions, summary } });
  } catch (error) {
    console.error('[SDE Portfolio Review API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Portfolio review failed' },
      { status: 500 },
    );
  }
}
