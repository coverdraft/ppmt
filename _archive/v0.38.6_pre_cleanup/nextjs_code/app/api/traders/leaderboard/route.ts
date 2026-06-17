import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/traders/leaderboard - Top traders by various metrics
export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');
    const searchParams = request.nextUrl.searchParams;
    const metric = searchParams.get('metric') || 'totalPnl';
    const chain = searchParams.get('chain') || undefined;
    const category = searchParams.get('category') || 'all';
    const limit = parseInt(searchParams.get('limit') || '25');

    const baseWhere: Record<string, unknown> = {};
    if (chain) baseWhere.chain = chain;

    // Category filters
    switch (category) {
      case 'smart_money':
        baseWhere.isSmartMoney = true;
        break;
      case 'whales':
        baseWhere.isWhale = true;
        break;
      case 'snipers':
        baseWhere.isSniper = true;
        break;
      case 'bots':
        baseWhere.isBot = true;
        break;
      case 'human':
        baseWhere.isBot = false;
        break;
    }

    // Valid sort metrics
    const validMetrics: Record<string, string> = {
      totalPnl: 'totalPnl',
      winRate: 'winRate',
      totalVolume: 'totalVolumeUsd',
      smartMoneyScore: 'smartMoneyScore',
      whaleScore: 'whaleScore',
      sniperScore: 'sniperScore',
      sharpeRatio: 'sharpeRatio',
      profitFactor: 'profitFactor',
      totalTrades: 'totalTrades',
      avgTradeSize: 'avgTradeSizeUsd',
      mevExtraction: 'mevExtractionUsd',
    };

    const sortField = validMetrics[metric] || 'totalPnl';

    const leaderboard = await db.trader.findMany({
      where: baseWhere,
      take: limit,
      orderBy: { [sortField]: 'desc' },
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
        totalPnl: true,
        winRate: true,
        totalVolumeUsd: true,
        smartMoneyScore: true,
        whaleScore: true,
        sniperScore: true,
        sharpeRatio: true,
        profitFactor: true,
        totalTrades: true,
        avgTradeSizeUsd: true,
        avgHoldTimeMin: true,
        mevExtractionUsd: true,
        lastActive: true,
        behaviorPatterns: {
          where: { confidence: { gte: 0.5 } },
          take: 2,
          orderBy: { confidence: 'desc' },
          select: { pattern: true, confidence: true },
        },
      },
    });

    // Format with rank
    const ranked = leaderboard.map((trader, index) => {
      const shortAddr = `${trader.address.slice(0, 6)}...${trader.address.slice(-4)}`;
      return {
        rank: index + 1,
        ...trader,
        pnlFormatted: trader.totalPnl >= 0
          ? `+$${trader.totalPnl.toFixed(0)}`
          : `-$${Math.abs(trader.totalPnl).toFixed(0)}`,
        shortAddress: shortAddr,
        displayName: trader.ensName || trader.solName || shortAddr,
      };
    });

    return NextResponse.json({
      metric,
      category,
      leaderboard: ranked,
    });
  } catch (error) {
    console.error('Error fetching leaderboard:', error);
    return NextResponse.json({ error: 'Failed to fetch leaderboard' }, { status: 500 });
  }
}
