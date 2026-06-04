import { NextRequest, NextResponse } from 'next/server';

/**
 * GET /api/brain/loops
 * Returns current backtest loop engine status and history.
 *
 * POST /api/brain/loops
 * Actions: start, stop, run
 */
export async function GET() {
  try {
    const { backtestLoopEngine } = await import('@/lib/services/backtesting/backtest-loop-engine');
    const status = backtestLoopEngine.getStatus();
    const history = await backtestLoopEngine.getLoopHistory(20);
    return NextResponse.json({ success: true, data: { ...status, history } });
  } catch (error: any) {
    console.error('[/api/brain/loops] GET Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const { backtestLoopEngine } = await import('@/lib/services/backtesting/backtest-loop-engine');
    const body = await request.json();
    const { action, config } = body;

    switch (action) {
      case 'start': {
        const result = await backtestLoopEngine.start(config);
        return NextResponse.json({ success: true, data: result });
      }
      case 'stop': {
        const result = backtestLoopEngine.stop();
        return NextResponse.json({ success: true, data: result });
      }
      case 'run': {
        const report = await backtestLoopEngine.runLoop();
        return NextResponse.json({ success: true, data: report });
      }
      default:
        return NextResponse.json({
          error: `Unknown action: ${action}`,
          availableActions: ['start', 'stop', 'run'],
        }, { status: 400 });
    }
  } catch (error: any) {
    console.error('[/api/brain/loops] POST Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
