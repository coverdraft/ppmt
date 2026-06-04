import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const body = await request.json();
    const { eventType, tokenId, walletAddress, entryPrice, stopLoss, takeProfit, pnl } = body;

    const userEvent = await db.userEvent.create({
      data: {
        eventType,
        tokenId,
        walletAddress,
        entryPrice,
        stopLoss,
        takeProfit,
        pnl,
      },
    });

    return NextResponse.json({ userEvent }, { status: 201 });
  } catch (error) {
    console.error('Error creating user event:', error);
    return NextResponse.json({ error: 'Failed to create user event' }, { status: 500 });
  }
}
