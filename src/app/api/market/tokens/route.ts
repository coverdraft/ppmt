import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { validateOrError, tokenQuerySchema } from '@/lib/validations';
import { normalizeChain, getChainVariants } from '@/lib/format';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/tokens
 *
 * SERVES FROM DB ONLY. No external API calls here.
 * The background refresh is handled by /api/brain/init scheduler.
 * This prevents OOM crashes from external API calls.
 *
 * Each token includes a `status` field:
 *   - 'LIVE': Has pairAddress (enriched by DexScreener/DexPaprika)
 *   - 'ACTIVE': Has liquidity > 0 AND volume24h > 0 (real CoinGecko data)
 *   - 'DISCOVERED': In DB but no enrichment yet
 */

interface TokenData {
  id: string;
  symbol: string;
  name: string;
  chain: string;
  address: string;
  priceUsd: number;
  volume24h: number;
  liquidity: number;
  marketCap: number;
  priceChange5m: number;
  priceChange15m: number;
  priceChange1h: number;
  priceChange24h: number;
  priceChange6h: number;
  riskScore?: number;
  status: 'LIVE' | 'ACTIVE' | 'DISCOVERED';
  pairAddress?: string | null;
  dexId?: string | null;
}

function determineTokenStatus(token: {
  pairAddress: string | null;
  liquidity: number;
  volume24h: number;
}): 'LIVE' | 'ACTIVE' | 'DISCOVERED' {
  if (token.pairAddress) return 'LIVE';
  if (token.liquidity > 0 && token.volume24h > 0) return 'ACTIVE';
  return 'DISCOVERED';
}

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;

  // Validate query params with Zod schema
  const queryObj = Object.fromEntries(searchParams.entries());
  const validation = validateOrError(tokenQuerySchema, queryObj);
  if (!validation.success) {
    return NextResponse.json(
      { data: null, error: validation.error, source: 'error' },
      { status: 400 },
    );
  }

  const chain = (searchParams.get('chain') || 'ALL').toUpperCase();
  const limit = Math.min(parseInt(searchParams.get('limit') || '500', 10), 5000);

  try {
    // Build chain filter - include 'ALL' chain tokens (CoinGecko top tokens) for any chain
    let chainFilter: any = undefined;
    if (chain !== 'ALL') {
      const canonical = normalizeChain(chain);
      if (canonical === 'EVM') {
        chainFilter = { in: ['ETH', 'BASE', 'ARB', 'OP', 'BSC', 'MATIC'] };
      } else {
        // Use getChainVariants to catch legacy data stored under 'SOLANA', 'ETHEREUM', etc.
        const variants = getChainVariants(canonical);
        chainFilter = { in: [...variants, 'ALL'] };
      }
    }

    const offset = parseInt(searchParams.get('offset') || '0', 10);
    const search = searchParams.get('search') || '';
    const statusFilter = searchParams.get('status') || '';

    let where: any = chainFilter ? { chain: chainFilter } : {};
    if (search) {
      where.OR = [
        { symbol: { contains: search, mode: 'insensitive' } },
        { name: { contains: search, mode: 'insensitive' } },
      ];
    }
    if (statusFilter === 'live') {
      where.pairAddress = { not: null };
    } else if (statusFilter === 'active') {
      where.AND = [
        { liquidity: { gt: 0 } },
        { volume24h: { gt: 0 } },
      ];
    }

    const [dbTokens, totalCount] = await Promise.all([
      db.token.findMany({
        where,
        include: { dna: true },
        orderBy: { volume24h: 'desc' },
        take: limit,
        skip: offset,
      }),
      db.token.count({ where }),
    ]);

    const tokens: TokenData[] = dbTokens.map(t => {
      const status = determineTokenStatus(t);
      return {
        id: t.id,
        symbol: t.symbol,
        name: t.name,
        chain: t.chain,
        address: t.address,
        priceUsd: t.priceUsd,
        volume24h: t.volume24h,
        liquidity: t.liquidity,
        marketCap: t.marketCap,
        priceChange5m: t.priceChange5m,
        priceChange15m: t.priceChange15m,
        priceChange1h: t.priceChange1h,
        priceChange24h: t.priceChange24h,
        priceChange6h: t.priceChange6h,
        riskScore: t.dna?.riskScore ?? undefined,
        status,
        pairAddress: t.pairAddress,
        dexId: t.dexId,
      };
    });

    // Status counts
    const liveCount = tokens.filter(t => t.status === 'LIVE').length;
    const activeCount = tokens.filter(t => t.status === 'ACTIVE').length;
    const discoveredCount = tokens.filter(t => t.status === 'DISCOVERED').length;

    return NextResponse.json({
      data: tokens,
      error: null,
      source: tokens.length > 0 ? 'db' : 'empty',
      total: totalCount,
      offset,
      limit,
      hasMore: offset + tokens.length < totalCount,
      statusBreakdown: { live: liveCount, active: activeCount, discovered: discoveredCount },
    });
  } catch (error) {
    console.error('[/api/market/tokens] DB query failed:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch tokens', source: 'error' },
      { status: 500 },
    );
  }
}
