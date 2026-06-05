import { NextRequest, NextResponse } from 'next/server';
import { executionCostEngine, type CostEstimate } from '@/lib/services/execution/execution-cost-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/execution/cost
 * Estimate execution cost for a potential trade.
 * Body: { tokenAddress, chain, positionSizeUsd, direction, currentPrice, liquidity?, volume24h? }
 * Response: CostEstimate
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { tokenAddress, chain, positionSizeUsd, direction, currentPrice, liquidity, volume24h } = body;

    if (!tokenAddress || !chain || positionSizeUsd == null || !direction || currentPrice == null) {
      return NextResponse.json(
        { data: null, error: 'Missing required fields: tokenAddress, chain, positionSizeUsd, direction, currentPrice' },
        { status: 400 }
      );
    }

    const estimate: CostEstimate = await executionCostEngine.estimateCost({
      tokenAddress,
      chain,
      positionSizeUsd,
      direction: direction === 'LONG' ? 'BUY' : 'SELL',
      currentPrice,
      liquidity: liquidity ?? 100000,
      volume24h: volume24h ?? 500000,
    });

    return NextResponse.json({ data: estimate });
  } catch (error) {
    console.error('Error estimating execution cost:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to estimate execution cost' },
      { status: 500 }
    );
  }
}
