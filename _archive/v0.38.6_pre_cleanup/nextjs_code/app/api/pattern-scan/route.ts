import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const tokenAddress = searchParams.get('token');
    const chain = searchParams.get('chain') || 'SOL';

    if (!tokenAddress) {
      return NextResponse.json({ error: 'token parameter required' }, { status: 400 });
    }

    const { candlestickPatternEngine } = await import('@/lib/services/brain/candlestick-pattern-engine');
    const result = await candlestickPatternEngine.scanMultiTimeframe(tokenAddress, chain);

    return NextResponse.json({
      success: true,
      result,
    });
  } catch (error) {
    console.error('[PatternScan API] Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}
