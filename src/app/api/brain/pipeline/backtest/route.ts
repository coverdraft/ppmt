import { NextRequest, NextResponse } from 'next/server';

// ============================================================
// Types for the request/response
// ============================================================

interface BacktestConfig {
  maxIterations?: number;
  minImprovementPct?: number;
  autoGenerateSynthetic?: boolean;
  autoAdopt?: boolean;
  loopIntervalMs?: number;
}

interface BacktestPostBody {
  action: 'start' | 'stop' | 'status';
  config?: BacktestConfig;
}

// ============================================================
// POST /api/brain/pipeline/backtest
// Control the backtest loop engine
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body: BacktestPostBody = await request.json();

    // Validate action
    if (!body.action || !['start', 'stop', 'status'].includes(body.action)) {
      return NextResponse.json(
        { data: null, error: 'action must be one of: start, stop, status' },
        { status: 400 },
      );
    }

    // Lazy import to avoid startup issues
    const { brainAnalysisPipeline } = await import('@/lib/services/brain/brain-analysis-pipeline');
    const { backtestLoopEngine } = await import('@/lib/services/backtesting/backtest-loop-engine');

    switch (body.action) {
      // ── START ──
      case 'start': {
        const result = await brainAnalysisPipeline.startBacktestLoop(body.config);
        return NextResponse.json({
          data: result,
          error: null,
        }, { status: 200 });
      }

      // ── STOP ──
      case 'stop': {
        const result = brainAnalysisPipeline.stopBacktestLoop();
        return NextResponse.json({
          data: result,
          error: null,
        }, { status: 200 });
      }

      // ── STATUS ──
      case 'status': {
        const status = backtestLoopEngine.getStatus();
        return NextResponse.json({
          data: {
            isRunning: status.isRunning,
            loopsCompleted: status.loopsCompleted,
            currentLoop: status.currentLoop,
            config: status.config,
          },
          error: null,
        }, { status: 200 });
      }
    }
  } catch (error) {
    console.error('[/api/brain/pipeline/backtest] POST error:', error);
    const message = error instanceof Error ? error.message : 'Backtest loop operation failed';
    return NextResponse.json(
      { data: null, error: message },
      { status: 500 },
    );
  }
}
