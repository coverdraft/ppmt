import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

/**
 * GET /api/data-monitor/summary
 * 
 * Lightweight data monitor summary - no external API calls, just DB counts.
 */
export async function GET() {
  try {
    // Count key tables in parallel
    const [
      tokens, dna, traders, transactions, signals, patterns,
      predictive, tradingSystems, backtests, candles, lifecycle,
      brainCycles, operabilitySnaps, feedback, behaviorModels,
      holdings, behaviorPatterns, labels, userEvents,
    ] = await Promise.all([
      db.token.count(),
      db.tokenDNA.count(),
      db.trader.count(),
      db.traderTransaction.count(),
      db.signal.count(),
      db.patternRule.count({ where: { isActive: true } }),
      db.predictiveSignal.count(),
      db.tradingSystem.count(),
      db.backtestRun.count(),
      db.priceCandle.count(),
      db.tokenLifecycleState.count(),
      db.brainCycleRun.count(),
      db.operabilitySnapshot.count(),
      db.feedbackMetrics.count(),
      db.traderBehaviorModel.count(),
      db.walletTokenHolding.count(),
      db.traderBehaviorPattern.count(),
      db.traderLabelAssignment.count(),
      db.userEvent.count(),
    ]);

    // Chain distribution
    const chainGroups = await db.token.groupBy({ by: ['chain'], _count: { chain: true } });
    const chainDistribution: Record<string, number> = {};
    for (const g of chainGroups) chainDistribution[g.chain] = g._count.chain;

    // Data freshness
    const recentUpdates = await db.token.count({ where: { updatedAt: { gte: new Date(Date.now() - 3600000) } } });

    // Prediction accuracy
    const validated = await db.predictiveSignal.count({ where: { wasCorrect: { not: null } } });
    const correct = await db.predictiveSignal.count({ where: { wasCorrect: true } });

    // Candle coverage
    const candleByTF = await db.priceCandle.groupBy({ by: ['timeframe'], _count: { timeframe: true } });
    const candleCoverage: Record<string, number> = {};
    for (const c of candleByTF) candleCoverage[c.timeframe] = c._count.timeframe;

    // Data gaps & recommendations
    const gaps: string[] = [];
    const recommendations: string[] = [];

    if (tokens < 50) gaps.push('Low token count');
    if (traders < 100) { gaps.push('Few traders profiled'); recommendations.push('Need more wallet data - add trader discovery from DexScreener top traders'); }
    if (candles < 5000) { gaps.push('Low candle count'); recommendations.push('Run OHLCV backfill for more historical data'); }
    if (patterns < 20) { gaps.push('Few patterns'); recommendations.push('Run pattern scan on tokens with candle data'); }
    if (validated < predictive * 0.5) recommendations.push('Many unvalidated predictions - run validation loop');

    const totalRecords = tokens + dna + traders + transactions + signals + patterns +
      predictive + tradingSystems + backtests + candles + lifecycle + brainCycles +
      operabilitySnaps + feedback + behaviorModels + holdings + behaviorPatterns + labels + userEvents;

    return NextResponse.json({
      success: true,
      data: {
        timestamp: new Date().toISOString(),
        summary: {
          tokens, dna, traders, transactions, signals, patterns,
          predictive, tradingSystems, backtests, candles, lifecycle,
          brainCycles, operabilitySnaps, feedback, behaviorModels,
          holdings, behaviorPatterns, labels, userEvents,
          totalRecords,
          recentUpdates,
        },
        chainDistribution,
        candleCoverage,
        predictionAccuracy: {
          validated,
          correct,
          winRate: validated > 0 ? (correct / validated * 100).toFixed(1) + '%' : 'N/A',
          unvalidated: predictive - validated,
        },
        gaps,
        recommendations,
      },
    });
  } catch (error: any) {
    console.error('[/api/data-monitor/summary] Error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
