import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const { searchParams } = new URL(request.url);
    const limit = Math.min(parseInt(searchParams.get('limit') || '50'), 200);
    const eventType = searchParams.get('eventType');

    const where: Record<string, unknown> = {};
    if (eventType) {
      where.eventType = eventType;
    }

    const userEvents = await db.userEvent.findMany({
      where,
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    return NextResponse.json({ events: userEvents, total: userEvents.length });
  } catch (error) {
    console.error('Error fetching user events:', error);
    return NextResponse.json({ events: [], total: 0, error: 'Failed to fetch user events' }, { status: 500 });
  }
}

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
