import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/backtest/walk-forward
 */
export async function POST(request: NextRequest) {
  try {
    const wfeModule = await import('@/lib/services/backtesting/walk-forward-engine');
    const walkForwardEngine = wfeModule.walkForwardEngine;
    const bdbModule = await import('@/lib/services/backtesting/backtest-data-bridge');
    const backtestDataBridge = bdbModule.backtestDataBridge;
    const tseModule = await import('@/lib/services/strategy/trading-system-engine');
    const tradingSystemEngine = tseModule.tradingSystemEngine;

    const body = await request.json();
    const {
      systemName,
      startDate,
      endDate,
      windowCount = 5,
      trainRatio = 0.7,
      initialCapital = 10,
      minWFE = 0.5,
      anchored = false,
      chain = 'SOL',
    } = body as {
      systemName?: string;
      startDate?: string;
      endDate?: string;
      windowCount?: number;
      trainRatio?: number;
      initialCapital?: number;
      minWFE?: number;
      anchored?: boolean;
      chain?: string;
    };

    if (!systemName) {
      return NextResponse.json(
        { data: null, error: 'systemName is required' },
        { status: 400 },
      );
    }

    // Get system template
    const systemTemplate = tradingSystemEngine.getTemplate(systemName);
    if (!systemTemplate) {
      return NextResponse.json(
        { data: null, error: `System template "${systemName}" not found` },
        { status: 404 },
      );
    }

    const start = startDate ? new Date(startDate) : new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    const end = endDate ? new Date(endDate) : new Date();

    if (start >= end) {
      return NextResponse.json(
        { data: null, error: 'startDate must be before endDate' },
        { status: 400 },
      );
    }

    // Load token data
    const tokenData = await backtestDataBridge.loadTokensForBacktest({
      startDate: start,
      endDate: end,
      timeframe: systemTemplate.primaryTimeframe,
      chain,
      minCandles: 20,
      assetFilter: systemTemplate.assetFilter,
      maxTokens: 10,
    });

    if (tokenData.length === 0) {
      return NextResponse.json({
        data: {
          recommendation: 'INSUFFICIENT_DATA',
          message: 'No token data available. Run OHLCV backfill first.',
        },
      });
    }

    // Run Walk-Forward Analysis
    const wfaResult = await walkForwardEngine.runWalkForwardAnalysis(
      {
        system: systemTemplate,
        startDate: start,
        endDate: end,
        windowCount,
        trainRatio,
        initialCapital,
        feesPct: 0.003,
        slippagePct: 0.5,
        minWFE,
        anchored,
      },
      tokenData,
    );

    // Generate human-readable summary
    const summary = walkForwardEngine.generateWFASummary(wfaResult);

    return NextResponse.json({
      data: {
        id: wfaResult.id,
        systemName: wfaResult.systemName,
        recommendation: wfaResult.recommendation,
        isRobust: wfaResult.isRobust,
        aggregateWFE: wfaResult.aggregateWFE,
        avgInSampleReturn: wfaResult.avgInSampleReturn,
        avgOutOfSampleReturn: wfaResult.avgOutOfSampleReturn,
        performanceConsistency: wfaResult.performanceConsistency,
        overallDegradation: wfaResult.overallDegradation,
        parameterStability: wfaResult.parameterStability,
        windows: wfaResult.windows.map(w => ({
          windowIndex: w.windowIndex,
          trainStart: w.trainStart,
          trainEnd: w.trainEnd,
          testStart: w.testStart,
          testEnd: w.testEnd,
          degradationPct: w.degradationPct,
          wfe: w.wfe,
          inSampleReturn: w.inSampleResult?.totalReturnPct ?? null,
          outOfSampleReturn: w.outOfSampleResult?.totalReturnPct ?? null,
          inSampleTrades: w.inSampleResult?.totalTrades ?? 0,
          outOfSampleTrades: w.outOfSampleResult?.totalTrades ?? 0,
        })),
        summary,
        tokensAnalyzed: tokenData.length,
      },
    });
  } catch (error) {
    console.error('Error running walk-forward analysis:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run walk-forward analysis' },
      { status: 500 },
    );
  }
}
