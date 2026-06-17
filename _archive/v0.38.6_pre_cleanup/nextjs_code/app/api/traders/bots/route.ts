import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/traders/bots - Get all identified bots with classification
export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const searchParams = request.nextUrl.searchParams;
    const botType = searchParams.get('botType') || undefined;
    const chain = searchParams.get('chain') || undefined;
    const minConfidence = parseFloat(searchParams.get('minConfidence') || '0.5');
    const limit = parseInt(searchParams.get('limit') || '50');
    const sortBy = searchParams.get('sortBy') || 'botConfidence';

    const where: Record<string, unknown> = {
      isBot: true,
      botConfidence: { gte: minConfidence },
    };
    
    if (botType) where.botType = botType;
    if (chain) where.chain = chain;

    const bots = await db.trader.findMany({
      where,
      take: limit,
      orderBy: { [sortBy]: 'desc' },
      include: {
        behaviorPatterns: { where: { confidence: { gte: 0.5 } }, orderBy: { confidence: 'desc' } },
        _count: { select: { transactions: true, tokenHoldings: true } },
      },
    });

    // Aggregate bot statistics
    const botTypeBreakdown = await db.trader.groupBy({
      by: ['botType'],
      where: { isBot: true },
      _count: { id: true },
      _avg: { 
        winRate: true,
        botConfidence: true,
        totalPnl: true,
        mevExtractionUsd: true,
      },
      _sum: {
        totalVolumeUsd: true,
        mevExtractionUsd: true,
        frontrunCount: true,
        sandwichCount: true,
      },
    });

    const chainBreakdown = await db.trader.groupBy({
      by: ['chain'],
      where: { isBot: true },
      _count: { id: true },
      _sum: { totalVolumeUsd: true },
    });

    const totalBots = await db.trader.count({ where: { isBot: true } });
    const totalMevExtracted = bots.reduce((sum, b) => sum + b.mevExtractionUsd, 0);
    const totalFrontruns = bots.reduce((sum, b) => sum + b.frontrunCount, 0);
    const totalSandwiches = bots.reduce((sum, b) => sum + b.sandwichCount, 0);

    return NextResponse.json({
      bots,
      stats: {
        totalBots,
        totalMevExtracted,
        totalFrontruns,
        totalSandwiches,
        botTypeBreakdown: botTypeBreakdown.map(b => ({
          type: b.botType,
          count: b._count.id,
          avgWinRate: b._avg.winRate,
          avgConfidence: b._avg.botConfidence,
          totalVolume: b._sum.totalVolumeUsd,
          totalMevExtracted: b._sum.mevExtractionUsd,
          totalFrontruns: b._sum.frontrunCount,
          totalSandwiches: b._sum.sandwichCount,
        })),
        chainBreakdown: chainBreakdown.map(c => ({
          chain: c.chain,
          count: c._count.id,
          totalVolume: c._sum.totalVolumeUsd,
        })),
      },
    });
  } catch (error) {
    console.error('Error fetching bots:', error);
    return NextResponse.json({ error: 'Failed to fetch bots' }, { status: 500 });
  }
}
