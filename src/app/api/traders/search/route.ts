import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/traders/search - Search traders by address, name, label
export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const searchParams = request.nextUrl.searchParams;
    const query = searchParams.get('q') || '';
    const limit = parseInt(searchParams.get('limit') || '20');

    if (!query || query.length < 2) {
      return NextResponse.json({ traders: [], total: 0 });
    }

    const traders = await db.trader.findMany({
      where: {
        OR: [
          { address: { contains: query } },
          { ensName: { contains: query } },
          { solName: { contains: query } },
          { primaryLabel: { contains: query.toUpperCase() } },
          { botType: { contains: query.toUpperCase() } },
        ],
      },
      take: limit,
      orderBy: { totalVolumeUsd: 'desc' },
      select: {
        id: true,
        address: true,
        chain: true,
        ensName: true,
        solName: true,
        primaryLabel: true,
        isBot: true,
        botType: true,
        isSmartMoney: true,
        isWhale: true,
        isSniper: true,
        winRate: true,
        totalPnl: true,
        totalVolumeUsd: true,
        smartMoneyScore: true,
        lastActive: true,
      },
    });

    return NextResponse.json({
      traders: traders.map(t => ({
        ...t,
        shortAddress: `${t.address.slice(0, 6)}...${t.address.slice(-4)}`,
        displayName: t.ensName || t.solName || `${t.address.slice(0, 6)}...${t.address.slice(-4)}`,
      })),
      total: traders.length,
    });
  } catch (error) {
    console.error('Error searching traders:', error);
    return NextResponse.json({ error: 'Failed to search traders' }, { status: 500 });
  }
}
