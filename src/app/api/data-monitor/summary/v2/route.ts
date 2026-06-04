import { NextResponse } from 'next/server';

/**
 * GET /api/data-monitor/summary/v2
 * Lightweight data monitor - no external API calls, dynamic DB import only.
 */
export async function GET() {
  try {
    const { db } = await import('@/lib/db');
    
    const counts = await Promise.all([
      db.token.count(), db.tokenDNA.count(), db.trader.count(),
      db.traderTransaction.count(), db.signal.count(),
      db.patternRule.count({ where: { isActive: true } }),
      db.predictiveSignal.count(), db.tradingSystem.count(),
      db.backtestRun.count(), db.priceCandle.count(),
      db.tokenLifecycleState.count(), db.brainCycleRun.count(),
      db.operabilitySnapshot.count(), db.feedbackMetrics.count(),
      db.traderBehaviorModel.count(), db.walletTokenHolding.count(),
      db.traderBehaviorPattern.count(), db.traderLabelAssignment.count(),
      db.userEvent.count(),
    ]);

    const [chainGroups, validated, correct, candleByTF] = await Promise.all([
      db.token.groupBy({ by: ['chain'], _count: { chain: true } }),
      db.predictiveSignal.count({ where: { wasCorrect: { not: null } } }),
      db.predictiveSignal.count({ where: { wasCorrect: true } }),
      db.priceCandle.groupBy({ by: ['timeframe'], _count: { timeframe: true } }),
    ]);

    const chainDistribution = {};
    for (const g of chainGroups) chainDistribution[g.chain] = g._count.chain;
    const candleCoverage = {};
    for (const c of candleByTF) candleCoverage[c.timeframe] = c._count.timeframe;
    const totalRecords = counts.reduce((a, b) => a + b, 0);

    return NextResponse.json({
      success: true,
      data: {
        timestamp: new Date().toISOString(),
        summary: {
          tokens: counts[0], dna: counts[1], traders: counts[2],
          transactions: counts[3], signals: counts[4], patterns: counts[5],
          predictive: counts[6], tradingSystems: counts[7], backtests: counts[8],
          candles: counts[9], lifecycle: counts[10], brainCycles: counts[11],
          operabilitySnaps: counts[12], feedback: counts[13], behaviorModels: counts[14],
          holdings: counts[15], behaviorPatterns: counts[16], labels: counts[17],
          userEvents: counts[18], totalRecords,
        },
        chainDistribution,
        candleCoverage,
        predictionAccuracy: {
          validated,
          correct,
          winRate: validated > 0 ? (correct / validated * 100).toFixed(1) + '%' : 'N/A',
        },
      },
    });
  } catch (error: unknown) {
    console.error('Error:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
