import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * POST /api/ppmt/realtime/trade
 *
 * Execute a paper trade from a PPMT signal.
 * Called when auto-trade is enabled and a trade signal fires (ENTRY_LONG or ENTRY_SHORT).
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const signal = body.signal as {
      symbol: string;
      timeframe: string;
      timestamp: number;
      current_price: number;
      prediction: {
        direction: string;
        confidence: number;
        expected_move_pct: number;
      };
      signal: {
        signal_type: string;
        confidence: number;
        entry_price: number | null;
        sl_price: number | null;
        tp_price: number | null;
        expected_move_pct: number;
        risk_reward_ratio: number;
        win_rate: number;
      } | null;
    };

    if (!signal || !signal.signal) {
      return NextResponse.json(
        { success: false, error: 'No signal provided or signal has no trade signal' },
        { status: 400 }
      );
    }

    const signalType = signal.signal.signal_type;

    // Only execute for entry signals
    if (signalType !== 'ENTRY_LONG' && signalType !== 'ENTRY_SHORT') {
      return NextResponse.json({
        success: false,
        error: `Signal type "${signalType}" is not a trade signal (requires ENTRY_LONG or ENTRY_SHORT)`,
      }, { status: 400 });
    }

    // Import paper trading engine
    const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');

    // Check for duplicate positions — prevent opening multiple positions on the same symbol
    const tokenSymbol = signal.symbol.replace('/', '');
    const openPositions = paperTradingEngine.getOpenPositions();
    const existingPosition = openPositions.find(
      (p) =>
        (p.symbol === tokenSymbol || p.symbol === signal.symbol) &&
        (p.systemName === 'PPMT_Auto' || p.strategyName === 'PPMT_Auto')
    );

    if (existingPosition) {
      return NextResponse.json({
        success: false,
        error: `Already have an open PPMT_Auto position for ${signal.symbol}`,
        existingPosition: {
          id: existingPosition.id,
          symbol: existingPosition.symbol,
          direction: existingPosition.direction,
          entryPrice: existingPosition.entryPrice,
          unrealizedPnl: existingPosition.unrealizedPnl,
        },
      }, { status: 409 });
    }

    // Execute the trade via paper trading engine
    const result = await paperTradingEngine.activateStrategy({
      tokenAddress: '',
      tokenSymbol,
      chain: 'binance',
      strategyName: 'PPMT_Auto',
      direction: signalType === 'ENTRY_LONG' ? 'LONG' : 'SHORT',
      operabilityScore: signal.prediction.confidence,
    });

    return NextResponse.json({
      success: result.success,
      data: {
        positionId: result.positionId,
        message: result.message,
        symbol: signal.symbol,
        direction: signalType === 'ENTRY_LONG' ? 'LONG' : 'SHORT',
        entryPrice: signal.current_price,
        confidence: signal.prediction.confidence,
      },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Trade execution failed';
    console.error('[PPMT-RT-TRADE] Error:', message);
    return NextResponse.json(
      { success: false, error: message },
      { status: 500 }
    );
  }
}
