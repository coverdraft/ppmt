import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Lazy db import
async function getDb() { return (await import('@/lib/db')).db; }

// GET /api/traders - List traders with filters
export async function GET(request: NextRequest) {
  try {
    const db = await getDb();
    const searchParams = request.nextUrl.searchParams;
    const chain = searchParams.get('chain') || undefined;
    const label = searchParams.get('label') || undefined;
    const isBot = searchParams.get('isBot');
    const isSmartMoney = searchParams.get('isSmartMoney');
    const isWhale = searchParams.get('isWhale');
    const isSniper = searchParams.get('isSniper');
    const botType = searchParams.get('botType') || undefined;
    const limit = Math.min(parseInt(searchParams.get('limit') || '50'), 500);
    const offset = parseInt(searchParams.get('offset') || '0');
    const sortBy = searchParams.get('sortBy') || 'totalPnl';
    const sortOrder = searchParams.get('sortOrder') || 'desc';
    const search = searchParams.get('search') || undefined;

    const where: Record<string, unknown> = {};
    
    if (chain) where.chain = chain;
    if (label) where.primaryLabel = label;
    if (isBot === 'true') where.isBot = true;
    if (isBot === 'false') where.isBot = false;
    if (isSmartMoney === 'true') where.isSmartMoney = true;
    if (isWhale === 'true') where.isWhale = true;
    if (isSniper === 'true') where.isSniper = true;
    if (botType) where.botType = botType;
    if (search) {
      where.OR = [
        { address: { contains: search } },
        { ensName: { contains: search } },
        { solName: { contains: search } },
      ];
    }

    const [traders, total] = await Promise.all([
      db.trader.findMany({
        where,
        take: limit,
        skip: offset,
        orderBy: { [sortBy]: sortOrder },
        include: {
          behaviorPatterns: { take: 3, orderBy: { confidence: 'desc' } },
          labelAssignments: true,
          _count: { select: { transactions: true, tokenHoldings: true } },
        },
      }),
      db.trader.count({ where }),
    ]);

    // Compute aggregate stats
    const stats = {
      totalTraders: total,
      bots: await db.trader.count({ where: { ...where, isBot: true } }),
      smartMoney: await db.trader.count({ where: { ...where, isSmartMoney: true } }),
      whales: await db.trader.count({ where: { ...where, isWhale: true } }),
      snipers: await db.trader.count({ where: { ...where, isSniper: true } }),
      avgWinRate: traders.length > 0
        ? traders.reduce((sum, t) => sum + t.winRate, 0) / traders.length
        : 0,
      totalVolume: traders.reduce((sum, t) => sum + t.totalVolumeUsd, 0),
    };

    return NextResponse.json({
      traders,
      stats,
      pagination: { total, limit, offset, hasMore: offset + limit < total },
    });
  } catch (error) {
    console.error('Error fetching traders:', error);
    return NextResponse.json({ error: 'Failed to fetch traders' }, { status: 500 });
  }
}
