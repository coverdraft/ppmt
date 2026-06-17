import { NextResponse } from 'next/server';

/**
 * GET /api/brain/status
 * 
 * Lightweight brain status - only uses DB, no heavy service imports.
 */
export async function GET() {
  try {
    const { db } = await import('@/lib/db');
    
    const [
      candleCount,
      lifecycleCount,
      behaviorCount,
      feedbackCount,
      evolutionCount,
      comparativeCount,
      signalCount,
      unvalidatedCount,
      tokenCount,
      traderCount,
      backtestCount,
      tradingSystemCount,
      patternCount,
      cycleCount,
      operabilityCount,
      dnaCount,
    ] = await Promise.all([
      db.priceCandle.count(),
      db.tokenLifecycleState.count(),
      db.traderBehaviorModel.count(),
      db.feedbackMetrics.count(),
      db.systemEvolution.count(),
      db.comparativeAnalysis.count(),
      db.predictiveSignal.count(),
      db.predictiveSignal.count({ where: { wasCorrect: null } }),
      db.token.count(),
      db.trader.count(),
      db.backtestRun.count(),
      db.tradingSystem.count(),
      db.patternRule.count({ where: { isActive: true } }),
      db.brainCycleRun.count(),
      db.operabilitySnapshot.count(),
      db.tokenDNA.count(),
    ]);

    // Validate predictions
    const validatedCount = await db.predictiveSignal.count({ where: { wasCorrect: { not: null } } });
    const correctCount = await db.predictiveSignal.count({ where: { wasCorrect: true } });

    return NextResponse.json({
      success: true,
      data: {
        // Data
        ohlcvCandles: candleCount,
        tokensTracked: tokenCount,
        tradersProfiled: traderCount,
        dnaProfiles: dnaCount,
        activePatterns: patternCount,
        tradingSystems: tradingSystemCount,
        
        // Brain engines
        lifecycleStates: lifecycleCount,
        behavioralModels: behaviorCount,
        feedbackMetrics: feedbackCount,
        systemEvolutions: evolutionCount,
        comparativeAnalyses: comparativeCount,
        
        // Operations
        backtestRuns: backtestCount,
        brainCycles: cycleCount,
        operabilitySnapshots: operabilityCount,
        
        // Signals
        totalSignals: signalCount,
        unvalidatedSignals: unvalidatedCount,
        validatedSignals: validatedCount,
        correctSignals: correctCount,
        winRate: validatedCount > 0 ? (correctCount / validatedCount * 100).toFixed(1) + '%' : 'N/A',
        
        // Health - combines signal state with scheduler operational status
        // IDLE:       No signals generated yet, Brain needs to be started
        // LEARNING:   Signals exist but none validated yet (Brain is new/learning)
        // ACTIVE:     Signals being generated, some pending validation (NORMAL running state)
        // HEALTHY:    All signals validated, Brain is well-calibrated
        brainHealth: signalCount === 0 ? 'IDLE' :
          validatedCount === 0 ? 'LEARNING' :
          unvalidatedCount > 0 ? 'ACTIVE' :
          'HEALTHY',
        brainStatusMessage:
          signalCount === 0 ? 'No signals yet — start the Brain scheduler to begin analysis' :
          validatedCount === 0 ? 'Brain is learning from new signals — validation pending' :
          unvalidatedCount > 0 ? `Brain is active — ${unvalidatedCount} signal(s) pending validation (this is normal during operation)` :
          'Brain is healthy — all signals have been validated',
        enginesWired: [
          'lifecycle', 'behavioral', 'feedback', 'ohlcv',
          'big-data', 'wallet-profiler', 'bot-detection',
          'operability', 'system-matcher', 'brain-orchestrator',
          'brain-cycle', 'capital-strategy',
        ],
      },
    });
  } catch (error: any) {
    console.error('[/api/brain/status] Error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
