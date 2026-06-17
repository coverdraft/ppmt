import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const searchParams = request.nextUrl.searchParams;
    const label = searchParams.get('label') || 'ALL';
    const limit = parseInt(searchParams.get('limit') || '50');

    let where: any = {};
    if (label !== 'ALL') {
      where.primaryLabel = label;
    }

    const wallets = await db.trader.findMany({
      where,
      orderBy: { winRate: 'desc' },
      take: limit,
    });

    return NextResponse.json({ wallets });
  } catch (error) {
    console.error('Error fetching wallets:', error);
    return NextResponse.json({ error: 'Failed to fetch wallets' }, { status: 500 });
  }
}
