import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const type = searchParams.get('type') || 'ALL';
    const minConfidence = parseInt(searchParams.get('minConfidence') || '0');
    const limit = parseInt(searchParams.get('limit') || '50');

    let where: any = {};

    if (type !== 'ALL') {
      where.type = type;
    }

    if (minConfidence > 0) {
      where.confidence = { gte: minConfidence };
    }

    const signals = await db.signal.findMany({
      where,
      include: { token: true },
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    // Flatten token data so frontend gets tokenSymbol + chain directly
    // Extract symbol from multiple fallback sources when token relation is missing
    const mapped = signals.map(s => {
      let symbol = s.token?.symbol ?? null;
      let name = s.token?.name ?? null;
      let chain = s.token?.chain ?? null;

      // Fallback 1: Extract from description
      if (!symbol && s.description) {
        const match = s.description.match(/:\s*([A-Z][A-Z0-9]{1,10})\s/);
        if (match) symbol = match[1];
      }

      // Fallback 2: Extract from metadata JSON
      if (!symbol && s.metadata) {
        try {
          const meta = typeof s.metadata === 'string' ? JSON.parse(s.metadata) : s.metadata;
          if (meta.tokenSymbol) symbol = meta.tokenSymbol as string;
          if (meta.chain && !chain) chain = meta.chain as string;
          if (meta.tokenName && !name) name = meta.tokenName as string;
        } catch {}
      }

      return {
        id: s.id,
        type: s.type,
        tokenId: s.tokenId,
        tokenSymbol: symbol || 'Unknown',
        tokenName: name,
        chain,
        confidence: s.confidence,
        direction: s.direction,
        description: s.description,
        priceTarget: s.priceTarget,
        metadata: s.metadata,
        createdAt: s.createdAt,
      };
    });

    return NextResponse.json({ signals: mapped });
  } catch (error) {
    console.error('Error fetching signals:', error);
    return NextResponse.json({ error: 'Failed to fetch signals' }, { status: 500 });
  }
}
