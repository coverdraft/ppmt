import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/execution/orders
 */
export async function GET(request: NextRequest) {
  try {
    const teModule = await import('@/lib/services/execution/trade-execution-engine');
    const tradeExecutionEngine = teModule.tradeExecutionEngine;
    const { searchParams } = new URL(request.url);
    const status = searchParams.get('status') as 'PENDING' | 'SUBMITTED' | 'CONFIRMED' | 'FAILED' | 'CANCELLED' | null;

    const orders = tradeExecutionEngine.getOrders(status || undefined);

    return NextResponse.json({
      data: orders.map(o => ({
        id: o.id,
        tokenAddress: o.tokenAddress,
        tokenSymbol: o.tokenSymbol,
        chain: o.chain,
        side: o.side,
        orderType: o.orderType,
        positionSizeUsd: o.positionSizeUsd,
        price: o.price,
        status: o.status,
        txHash: o.txHash,
        submittedAt: o.submittedAt,
        confirmedAt: o.confirmedAt,
        executedPrice: o.executedPrice,
        feesUsd: o.feesUsd,
        dexRoute: o.dexRoute,
        systemName: o.systemName,
        reason: o.reason,
        error: o.error,
      })),
      total: orders.length,
    });
  } catch (error) {
    console.error('Error getting execution orders:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get orders' },
      { status: 500 },
    );
  }
}
