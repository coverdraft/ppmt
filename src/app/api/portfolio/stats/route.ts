import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { strategyCorrelationService } from '@/lib/services/risk/strategy-correlation-service';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/portfolio/stats
 *
 * Returns comprehensive portfolio statistics:
 * - Sharpe, Sortino, Max DD, Win Rate, Total PnL
 * - Current positions summary
 * - Active strategies count + states distribution
 * - Rolling correlation avg
 * - Risk budget utilization
 */
export async function GET() {
  try {
    // Get the most recent session
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    const initialCapital = session?.initialCapital ?? 0;
    const currentCapital = session?.currentCapital ?? 0;

    // Get all trades for stats
    const trades = await db.paperTradingTrade.findMany({
      where: session ? { position: { runId: session.id } } : {},
      orderBy: { closedAt: 'asc' },
      select: {
        id: true,
        pnlUsd: true,
        pnlPct: true,
        closedAt: true,
        tokenSymbol: true,
        strategyName: true,
      },
    });

    // Open positions
    const openPositions = await db.paperTradingPosition.findMany({
      where: {
        status: 'OPEN',
        ...(session ? { runId: session.id } : {}),
      },
      select: {
        id: true,
        tokenSymbol: true,
        chain: true,
        sizeUsd: true,
        pnlUsd: true,
        pnlPct: true,
        strategyName: true,
      },
    });

    // Calculate stats
    const totalTrades = trades.length;
    const winningTrades = trades.filter(t => t.pnlUsd > 0).length;
    const losingTrades = trades.filter(t => t.pnlUsd <= 0).length;
    const winRate = totalTrades > 0 ? winningTrades / totalTrades : 0;

    const totalPnlUsd = trades.reduce((sum, t) => sum + t.pnlUsd, 0);
    const totalPnlPct = initialCapital > 0 ? ((currentCapital - initialCapital) / initialCapital) * 100 : 0;

    // Calculate daily returns for Sharpe/Sortino
    const dailyReturns = buildDailyReturns(trades, currentCapital);

    // Sharpe Ratio (annualized, risk-free rate = 0)
    const sharpeRatio = calculateSharpeRatio(dailyReturns);

    // Sortino Ratio (only penalizes downside deviation)
    const sortinoRatio = calculateSortinoRatio(dailyReturns);

    // Max Drawdown
    const maxDrawdownPct = calculateMaxDrawdown(trades, initialCapital);

    // Strategy states from DecisionAudit
    const latestAudits = await db.decisionAudit.findMany({
      orderBy: { timestamp: 'desc' },
      select: {
        strategyId: true,
        decision: true,
        timestamp: true,
      },
      take: 200,
    });

    // Get the latest state for each strategy
    const strategyStates = new Map<string, { state: string; name: string; timestamp: Date }>();
    for (const audit of latestAudits) {
      try {
        const decision = JSON.parse(audit.decision) as Record<string, unknown>;
        if (!strategyStates.has(audit.strategyId)) {
          strategyStates.set(audit.strategyId, {
            state: (decision.state as string) || 'UNKNOWN',
            name: (decision.strategyName as string) || audit.strategyId,
            timestamp: audit.timestamp,
          });
        }
      } catch {
        // Skip
      }
    }

    const stateDistribution = {
      ACTIVE: 0,
      CONDITIONAL: 0,
      PAUSED: 0,
      REJECTED: 0,
    };
    for (const [, s] of strategyStates) {
      if (s.state in stateDistribution) {
        (stateDistribution as Record<string, number>)[s.state]++;
      }
    }

    // Rolling correlation (avg pairwise)
    let rollingCorrelationAvg = 0;
    try {
      const matrix = await strategyCorrelationService.getCurrentCorrelationMatrix();
      rollingCorrelationAvg = strategyCorrelationService.getAverageCorrelation(matrix);
    } catch {
      // Not enough data
    }

    // Risk budget
    let riskBudget: Record<string, unknown> = {};
    try {
      const budget = await killSwitchService.loadRiskBudget();
      riskBudget = {
        maxPortfolioDrawdownPct: budget.maxPortfolioDrawdownPct,
        maxStrategyDrawdownPct: budget.maxStrategyDrawdownPct,
        maxPositionLossPct: budget.maxPositionLossPct,
        maxConcentrationPct: budget.maxConcentrationPct,
        maxChainPct: budget.maxChainPct,
        maxCorrelatedPct: budget.maxCorrelatedPct,
        riskProfile: budget.riskProfile,
      };
    } catch {
      // Defaults
    }

    // Risk budget utilization
    const currentDD = initialCapital > 0 && session
      ? Math.max(0, ((session.peakCapital - currentCapital) / session.peakCapital) * 100)
      : 0;
    const maxPortfolioDD = (riskBudget.maxPortfolioDrawdownPct as number) || 20;
    const ddUtilizationPct = maxPortfolioDD > 0 ? (currentDD / maxPortfolioDD) * 100 : 0;

    // Token concentration
    const tokenConcentration: Record<string, number> = {};
    for (const pos of openPositions) {
      const pct = currentCapital > 0 ? (pos.sizeUsd / currentCapital) * 100 : 0;
      tokenConcentration[pos.tokenSymbol] = (tokenConcentration[pos.tokenSymbol] || 0) + pct;
    }

    // Kill switch status
    const killSwitchState = killSwitchService.getStateSerializable();

    // Position summary
    const positionsSummary = openPositions.map(p => ({
      symbol: p.tokenSymbol,
      chain: p.chain,
      sizeUsd: p.sizeUsd,
      pnlUsd: p.pnlUsd,
      pnlPct: p.pnlPct,
      strategy: p.strategyName || 'Unknown',
    }));

    return NextResponse.json({
      data: {
        // Portfolio metrics
        sharpeRatio: Math.round(sharpeRatio * 100) / 100,
        sortinoRatio: Math.round(sortinoRatio * 100) / 100,
        maxDrawdownPct: Math.round(maxDrawdownPct * 100) / 100,
        winRate: Math.round(winRate * 10000) / 10000,
        totalPnlUsd: Math.round(totalPnlUsd * 100) / 100,
        totalPnlPct: Math.round(totalPnlPct * 100) / 100,

        // Capital
        initialCapital,
        currentCapital: Math.round(currentCapital * 100) / 100,
        currentDrawdownPct: Math.round(currentDD * 100) / 100,

        // Trades
        totalTrades,
        winningTrades,
        losingTrades,

        // Rolling correlation
        rollingCorrelationAvg: Math.round(rollingCorrelationAvg * 10000) / 10000,

        // Strategy states
        activeStrategies: strategyStates.size,
        stateDistribution,
        strategies: Array.from(strategyStates.entries()).map(([id, s]) => ({
          id,
          name: s.name,
          state: s.state,
          lastEvaluated: s.timestamp.toISOString(),
        })),

        // Risk budget
        riskBudget,
        riskBudgetUtilization: {
          drawdownUtilizationPct: Math.round(ddUtilizationPct * 100) / 100,
          currentDD,
          maxDD: maxPortfolioDD,
        },

        // Concentration
        tokenConcentration,
        maxTokenConcentration: Math.max(0, ...Object.values(tokenConcentration)),
        maxConcentrationLimit: riskBudget.maxConcentrationPct || 15,

        // Kill switch
        killSwitch: killSwitchState,

        // Positions
        openPositions: positionsSummary,
        openPositionCount: openPositions.length,
      },
    });
  } catch (error) {
    console.error('[Portfolio Stats API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Failed to fetch portfolio stats' },
      { status: 500 },
    );
  }
}

// ============================================================
// HELPERS
// ============================================================

function buildDailyReturns(trades: Array<{ pnlUsd: number; pnlPct: number; closedAt: Date }>, portfolioCapital: number): number[] {
  // Sum dollar PnL per day, then convert to portfolio return percentage
  // using previous day's equity as denominator (not constant capital)
  const dailyMap = new Map<string, number>();

  for (const trade of trades) {
    const dateKey = trade.closedAt.toISOString().split('T')[0];
    dailyMap.set(dateKey, (dailyMap.get(dateKey) || 0) + trade.pnlUsd);
  }

  let runningEquity = portfolioCapital;
  return Array.from(dailyMap.values()).map(dailyPnlUsd => {
    const dailyReturn = runningEquity > 0 ? (dailyPnlUsd / runningEquity) * 100 : 0;
    runningEquity += dailyPnlUsd;
    return dailyReturn;
  });
}

function calculateSharpeRatio(dailyReturns: number[]): number {
  if (dailyReturns.length < 2) return 0;

  const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
  const variance = dailyReturns.reduce((s, r) => s + Math.pow(r - mean, 2), 0) / (dailyReturns.length - 1);
  const stdDev = Math.sqrt(variance);

  if (stdDev === 0) return 0;

  // Annualize: daily return * sqrt(365) for crypto
  return (mean / stdDev) * Math.sqrt(365);
}

function calculateSortinoRatio(dailyReturns: number[]): number {
  if (dailyReturns.length < 2) return 0;

  const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
  const negativeReturns = dailyReturns.filter(r => r < 0);

  if (negativeReturns.length === 0) return mean > 0 ? Infinity : 0;

  const downVariance = negativeReturns.reduce((s, r) => s + Math.pow(r, 2), 0) / (negativeReturns.length || 1);
  const downDev = Math.sqrt(downVariance);

  if (downDev === 0) return 0;

  return (mean / downDev) * Math.sqrt(365);
}

function calculateMaxDrawdown(
  trades: Array<{ pnlUsd: number; closedAt: Date }>,
  initialCapital: number,
): number {
  if (trades.length === 0 || initialCapital === 0) return 0;

  let peak = initialCapital;
  let running = initialCapital;
  let maxDD = 0;

  for (const trade of trades) {
    running += trade.pnlUsd;
    if (running > peak) peak = running;
    const dd = ((peak - running) / peak) * 100;
    if (dd > maxDD) maxDD = dd;
  }

  return maxDD;
}
