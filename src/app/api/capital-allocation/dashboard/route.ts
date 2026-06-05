/**
 * GET /api/capital-allocation/dashboard
 *
 * Returns the full allocation dashboard data:
 * - Current portfolio state
 * - Per-strategy allocation + method
 * - Correlation matrix summary
 * - Kill switch status
 * - Risk budget config
 */

import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const { killSwitchService } = await import('@/lib/services/risk/kill-switch-service');
    const { strategyCorrelationService } = await import('@/lib/services/risk/strategy-correlation-service');
    const { db } = await import('@/lib/db');

    // ---- Portfolio State ----
    let totalCapital = 10000;
    let currentDD = 0;
    let peakCapital = 10000;
    let allocatedPct = 0;
    let availablePct = 100;
    let activeStrategies = 0;

    try {
      const session = await db.paperTradingSession.findFirst({
        orderBy: { createdAt: 'desc' },
      });
      if (session) {
        totalCapital = session.currentCapital;
        peakCapital = session.peakCapital;
        currentDD = peakCapital > 0
          ? Math.max(0, ((peakCapital - totalCapital) / peakCapital) * 100)
          : 0;
      }

      activeStrategies = await db.tradingSystem.count({
        where: { isActive: true },
      });
    } catch { /* use defaults */ }

    // ---- Per-Strategy Allocations ----
    const strategyAllocations: Array<{
      strategyId: string;
      strategyName: string;
      state: string;
      action: string;
      method: string;
      sizeUsd: number;
      targetPct: number;
      category: string;
    }> = [];

    try {
      const systems = await db.tradingSystem.findMany({
        where: { isActive: true },
        take: 20,
        orderBy: { updatedAt: 'desc' },
      });

      for (const sys of systems) {
        // Try to get latest decision from audit
        let state = 'ACTIVE';
        let action = 'MAINTAIN';
        let method = sys.allocationMethod || 'EQUAL_WEIGHT';
        let sizeUsd = totalCapital * (sys.maxPositionPct / 100) || totalCapital * 0.05;
        let targetPct = sys.maxPositionPct || 5;

        try {
          const latestAudit = await db.decisionAudit.findFirst({
            where: { strategyId: sys.id },
            orderBy: { timestamp: 'desc' },
          });
          if (latestAudit) {
            const decision = JSON.parse(latestAudit.decision) as Record<string, unknown>;
            state = (decision.state as string) || state;
            action = (decision.capitalAction as string) || action;
            const rec = decision.capitalRecommendation as Record<string, unknown> | undefined;
            if (rec) {
              method = (rec.method as string) || method;
              sizeUsd = (rec.sizeUsd as number) ?? sizeUsd;
              targetPct = (rec.targetPct as number) ?? targetPct;
            }
          }
        } catch { /* use defaults */ }

        strategyAllocations.push({
          strategyId: sys.id,
          strategyName: sys.name,
          state,
          action,
          method,
          sizeUsd: Math.round(sizeUsd * 100) / 100,
          targetPct: Math.round(targetPct * 100) / 100,
          category: sys.category,
        });
      }
    } catch { /* empty allocations */ }

    // Calculate allocated percentage
    const totalAllocated = strategyAllocations.reduce((s, a) => s + a.sizeUsd, 0);
    allocatedPct = totalCapital > 0 ? (totalAllocated / totalCapital) * 100 : 0;
    availablePct = Math.max(0, 100 - allocatedPct);

    // ---- Correlation Matrix ----
    let correlationSummary: {
      strategies: string[];
      matrix: number[][];
      avgCorrelation: number;
      dataPoints: number;
      computedAt: string;
    };

    try {
      const matrix = await strategyCorrelationService.getCurrentCorrelationMatrix();
      const avgCorr = strategyCorrelationService.getAverageCorrelation(matrix);
      correlationSummary = {
        strategies: matrix.strategies,
        matrix: matrix.matrix,
        avgCorrelation: Math.round(avgCorr * 10000) / 10000,
        dataPoints: matrix.dataPoints,
        computedAt: matrix.computedAt.toISOString(),
      };
    } catch {
      correlationSummary = {
        strategies: [],
        matrix: [],
        avgCorrelation: 0,
        dataPoints: 0,
        computedAt: new Date().toISOString(),
      };
    }

    // ---- Kill Switch Status ----
    let killSwitchStatus: Record<string, unknown>;
    try {
      killSwitchStatus = killSwitchService.getStateSerializable();
    } catch {
      killSwitchStatus = {
        globalPause: false,
        portfolioDDTriggered: false,
        strategyDDTriggered: [],
      };
    }

    // ---- Risk Budget Config ----
    let riskBudget: Record<string, unknown>;
    try {
      const budget = await killSwitchService.loadRiskBudget();
      riskBudget = {
        maxPortfolioDrawdownPct: budget.maxPortfolioDrawdownPct,
        maxStrategyDrawdownPct: budget.maxStrategyDrawdownPct,
        maxPositionLossPct: budget.maxPositionLossPct,
        maxConcentrationPct: budget.maxConcentrationPct,
        maxSectorPct: budget.maxSectorPct,
        maxChainPct: budget.maxChainPct,
        maxCorrelatedPct: budget.maxCorrelatedPct,
        riskProfile: budget.riskProfile,
      };
    } catch {
      riskBudget = {
        maxPortfolioDrawdownPct: 20,
        maxStrategyDrawdownPct: 30,
        maxPositionLossPct: 50,
        maxConcentrationPct: 15,
        maxSectorPct: 30,
        maxChainPct: 50,
        maxCorrelatedPct: 40,
        riskProfile: 'MODERATE',
      };
    }

    // ---- Allocation Method Distribution ----
    const methodDistribution: Record<string, number> = {};
    for (const alloc of strategyAllocations) {
      methodDistribution[alloc.method] = (methodDistribution[alloc.method] || 0) + 1;
    }

    return NextResponse.json({
      data: {
        portfolio: {
          totalCapital,
          peakCapital,
          currentDD: Math.round(currentDD * 100) / 100,
          allocatedPct: Math.round(allocatedPct * 100) / 100,
          availablePct: Math.round(availablePct * 100) / 100,
          activeStrategies,
        },
        strategyAllocations,
        correlationSummary,
        killSwitchStatus,
        riskBudget,
        methodDistribution,
        timestamp: new Date().toISOString(),
      },
    });
  } catch (error) {
    console.error('[CapitalAllocation/Dashboard] Error:', error);
    return NextResponse.json(
      { data: null, error: `Dashboard failed: ${error instanceof Error ? error.message : 'unknown'}` },
      { status: 500 },
    );
  }
}
