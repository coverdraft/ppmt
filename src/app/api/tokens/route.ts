import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const chain = searchParams.get('chain') || 'ALL';
    const risk = searchParams.get('risk') || 'ALL';
    const sort = searchParams.get('sort') || 'volume';
    const limit = Math.min(parseInt(searchParams.get('limit') || '500'), 2000);
    const trending = searchParams.get('trending') === 'true';

    let where: any = {};

    if (chain !== 'ALL') {
      where.chain = chain;
    }

    if (risk === 'SAFE') {
      where.dna = { riskScore: { lte: 30 } };
    } else if (risk === 'CAUTION') {
      where.dna = { riskScore: { gt: 30, lte: 60 } };
    } else if (risk === 'DANGER') {
      where.dna = { riskScore: { gt: 60 } };
    }

    if (trending) {
      where.volume24h = { gt: 1000000 };
    }

    let orderBy: any = {};
    switch (sort) {
      case 'volume':
        orderBy = { volume24h: 'desc' };
        break;
      case 'price_change':
        orderBy = { priceChange24h: 'desc' };
        break;
      case 'newest':
        orderBy = { createdAt: 'desc' };
        break;
      case 'market_cap':
        orderBy = { marketCap: 'desc' };
        break;
      default:
        orderBy = { volume24h: 'desc' };
    }

    const tokens = await db.token.findMany({
      where,
      include: { dna: true },
      orderBy,
      take: limit,
    });

    return NextResponse.json({ tokens });
  } catch (error) {
    console.error('Error fetching tokens:', error);
    return NextResponse.json({ error: 'Failed to fetch tokens' }, { status: 500 });
  }
}
