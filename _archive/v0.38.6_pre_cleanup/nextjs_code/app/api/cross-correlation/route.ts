import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const tokenAddress = searchParams.get('token');
    const chain = searchParams.get('chain') || 'SOL';
    const stats = searchParams.get('stats') === 'true';

    const { crossCorrelationEngine } = await import('@/lib/services/risk/cross-correlation-engine');

    if (stats) {
      const result = await crossCorrelationEngine.getCorrelationStats();
      return NextResponse.json({ success: true, stats: result });
    }

    if (!tokenAddress) {
      return NextResponse.json({ error: 'token parameter required' }, { status: 400 });
    }

    const result = await crossCorrelationEngine.analyzeCrossCorrelation(tokenAddress, chain);

    return NextResponse.json({
      success: true,
      result,
    });
  } catch (error) {
    console.error('[CrossCorrelation API] Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}
