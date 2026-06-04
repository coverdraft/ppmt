import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/token/[address]
 *
 * Fetches individual token data from DexScreener (by token address) and
 * enriches it with CoinGecko price / OHLCV data.
 *
 * Route params:
 *   address – token contract address (path parameter)
 *
 * Query params:
 *   chain – chain id (default: "solana")
 *
 * Rate-limit notes (free tiers):
 *   - DexScreener: ~300 req/min (unauthenticated)
 *   - CoinGecko:  ~10-30 req/min (free, no API key)
 */

// Lazy clients
let _dexClient: import('@/lib/services/data-sources/dexscreener-client').DexScreenerClient | null = null;
let _cgClient: import('@/lib/services/data-sources/coingecko-client').CoinGeckoClient | null = null;

async function getDexClient() {
  if (!_dexClient) {
    const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');
    _dexClient = dexScreenerClient;
  }
  return _dexClient;
}

async function getCoinGeckoClient() {
  if (!_cgClient) {
    const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');
    _cgClient = coinGeckoClient;
  }
  return _cgClient;
}

interface TokenDetail {
  id: string;
  symbol: string;
  name: string;
  chain: string;
  address: string;
  priceUsd: number;
  priceNative: string;
  volume24h: number;
  volume6h: number;
  volume1h: number;
  liquidity: number;
  marketCap: number;
  fdv: number;
  priceChange5m: number;
  priceChange15m: number;
  priceChange1h: number;
  priceChange24h: number;
  riskScore: number | null;
  txns24h: { buys: number; sells: number };
  txns6h: { buys: number; sells: number };
  txns1h: { buys: number; sells: number };
  dexId: string;
  pairAddress: string;
  pairUrl: string;
  quoteToken: { address: string; symbol: string; name: string };
  imageUrl?: string;
  websites?: { url: string }[];
  socials?: { type: string; url: string }[];
  pairCreatedAt: number | null;
  ohlcv: Array<{
    unixTime: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
}

function formatTokenDetail(
  pair: any,
  priceChange24h?: number,
  price?: number,
  ohlcv: TokenDetail['ohlcv'] = [],
): TokenDetail {
  return {
    id: pair.pairAddress ?? pair.address,
    symbol: pair.baseToken?.symbol ?? '',
    name: pair.baseToken?.name ?? '',
    chain: pair.chainId ?? 'solana',
    address: pair.baseToken?.address ?? pair.address,
    priceUsd: price ?? (parseFloat(pair.priceUsd) || 0),
    priceNative: pair.priceNative ?? '',
    volume24h: pair.volume?.h24 ?? 0,
    volume6h: pair.volume?.h6 ?? 0,
    volume1h: pair.volume?.h1 ?? 0,
    liquidity: pair.liquidity?.usd ?? 0,
    marketCap: pair.marketCap ?? 0,
    fdv: pair.fdv ?? 0,
    priceChange5m: pair.priceChange?.m5 ?? 0,
    priceChange15m: 0,
    priceChange1h: pair.priceChange?.h1 ?? 0,
    priceChange24h: priceChange24h ?? pair.priceChange?.h24 ?? 0,
    riskScore: null,
    txns24h: pair.txns?.h24 ?? { buys: 0, sells: 0 },
    txns6h: pair.txns?.h6 ?? { buys: 0, sells: 0 },
    txns1h: pair.txns?.h1 ?? { buys: 0, sells: 0 },
    dexId: pair.dexId ?? '',
    pairAddress: pair.pairAddress ?? '',
    pairUrl: pair.pairAddress ? `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}` : '',
    quoteToken: pair.quoteToken ?? { address: '', symbol: '', name: '' },
    imageUrl: pair.info?.imageUrl,
    websites: pair.info?.websites,
    socials: pair.info?.socials,
    pairCreatedAt: pair.pairCreatedAt ?? null,
    ohlcv,
  };
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ address: string }> },
) {
  const { address } = await params;
  const searchParams = request.nextUrl.searchParams;
  const chain = searchParams.get('chain') || 'solana';

  if (!address) {
    return NextResponse.json(
      { data: null, error: 'Token address is required', source: 'live' as const },
      { status: 400 },
    );
  }

  try {
    const dexClient = await getDexClient();
    const cgClient = await getCoinGeckoClient();

    // Fetch from DexScreener (uses token address endpoint)
    const pairs = await dexClient.searchTokenPairs(address);
    const pair = pairs?.[0] ?? null;

    if (pair) {
      // Enrich with CoinGecko OHLCV data in parallel
      let ohlcvData: TokenDetail['ohlcv'] = [];
      try {
        // Try to find the CoinGecko coin ID for OHLCV
        const coinId = await cgClient.getCoinIdFromContract(chain, address);
        if (coinId) {
          const candles = await cgClient.getOHLCV(coinId, 1);
          ohlcvData = candles.map(c => ({
            unixTime: Math.floor(c.timestamp / 1000),
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: 0, // CoinGecko OHLCV doesn't provide volume
          }));
        }
      } catch {
        // CoinGecko OHLCV not available for this token - that's OK
      }

      const detail = formatTokenDetail(pair, undefined, undefined, ohlcvData);

      // Persist to DB for cache/fallback (fire-and-forget)
      persistToken(detail).catch(() => {});

      return NextResponse.json({
        data: detail,
        error: null,
        source: 'live' as const,
      });
    }

    // If no pair found on DexScreener, try CoinGecko as secondary source
    try {
      const coinDetail = await cgClient.getTokenByContract(chain, address);
      if (coinDetail) {
        const detail: TokenDetail = {
          id: coinDetail.id,
          symbol: coinDetail.symbol?.toUpperCase() ?? '',
          name: coinDetail.name ?? '',
          chain,
          address,
          priceUsd: coinDetail.market_data?.current_price?.usd ?? 0,
          priceNative: '',
          volume24h: coinDetail.market_data?.total_volume?.usd ?? 0,
          volume6h: 0,
          volume1h: 0,
          liquidity: 0,
          marketCap: coinDetail.market_data?.market_cap?.usd ?? 0,
          fdv: 0,
          priceChange5m: 0,
          priceChange15m: 0,
          priceChange1h: coinDetail.market_data?.price_change_percentage_1h ?? 0,
          priceChange24h: coinDetail.market_data?.price_change_percentage_24h ?? 0,
          riskScore: null,
          txns24h: { buys: 0, sells: 0 },
          txns6h: { buys: 0, sells: 0 },
          txns1h: { buys: 0, sells: 0 },
          dexId: '',
          pairAddress: '',
          pairUrl: '',
          quoteToken: { address: '', symbol: '', name: '' },
          pairCreatedAt: null,
          ohlcv: [],
        };

        // Fetch OHLCV from CoinGecko
        try {
          const candles = await cgClient.getOHLCV(coinDetail.id, 1);
          detail.ohlcv = candles.map(c => ({
            unixTime: Math.floor(c.timestamp / 1000),
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: 0,
          }));
        } catch { /* skip */ }

        return NextResponse.json({
          data: detail,
          error: null,
          source: 'live' as const,
        });
      }
    } catch {
      // CoinGecko also failed – fall through to DB fallback
    }
  } catch (error) {
    console.error('[/api/market/token/[address]] Live fetch failed:', error);
  }

  try {
    const { db } = await import('@/lib/db');
    const dbToken = await db.token.findFirst({
      where: {
        OR: [
          { address },
          { id: address },
        ],
      },
      include: {
        dna: true,
        signals: {
          orderBy: { createdAt: 'desc' },
          take: 10,
        },
      },
    });

    if (!dbToken) {
      return NextResponse.json(
        { data: null, error: 'Token not found', source: 'fallback' as const },
        { status: 404 },
      );
    }

    const detail: TokenDetail = {
      id: dbToken.id,
      symbol: dbToken.symbol,
      name: dbToken.name,
      chain: dbToken.chain,
      address: dbToken.address,
      priceUsd: dbToken.priceUsd,
      priceNative: '',
      volume24h: dbToken.volume24h,
      volume6h: 0,
      volume1h: 0,
      liquidity: dbToken.liquidity,
      marketCap: dbToken.marketCap,
      fdv: 0,
      priceChange5m: dbToken.priceChange5m,
      priceChange15m: dbToken.priceChange15m,
      priceChange1h: dbToken.priceChange1h,
      priceChange24h: dbToken.priceChange24h,
      riskScore: dbToken.dna?.riskScore ?? null,
      txns24h: { buys: 0, sells: 0 },
      txns6h: { buys: 0, sells: 0 },
      txns1h: { buys: 0, sells: 0 },
      dexId: dbToken.dexId ?? '',
      pairAddress: dbToken.pairAddress ?? '',
      pairUrl: dbToken.pairUrl ?? '',
      quoteToken: { address: '', symbol: '', name: '' },
      pairCreatedAt: null,
      ohlcv: [],
    };

    return NextResponse.json({
      data: detail,
      error: null,
      source: 'fallback' as const,
    });
  } catch (dbError) {
    console.error('[/api/market/token/[address]] DB fallback also failed:', dbError);
    return NextResponse.json(
      {
        data: null,
        error: 'Token lookup failed from live source and database',
        source: 'fallback' as const,
      },
      { status: 500 },
    );
  }
}

async function persistToken(detail: TokenDetail) {
  try {
    const { db } = await import('@/lib/db');
    await db.token.upsert({
      where: { address: detail.address || detail.id },
      update: {
        symbol: detail.symbol,
        name: detail.name,
        priceUsd: detail.priceUsd,
        volume24h: detail.volume24h,
        liquidity: detail.liquidity,
        marketCap: detail.marketCap,
        priceChange24h: detail.priceChange24h,
        dexId: detail.dexId || null,
        pairAddress: detail.pairAddress || null,
        pairUrl: detail.pairUrl || null,
      },
      create: {
        address: detail.address || detail.id,
        symbol: detail.symbol,
        name: detail.name,
        chain: detail.chain.toUpperCase(),
        priceUsd: detail.priceUsd,
        volume24h: detail.volume24h,
        liquidity: detail.liquidity,
        marketCap: detail.marketCap,
        priceChange24h: detail.priceChange24h,
        dexId: detail.dexId || null,
        pairAddress: detail.pairAddress || null,
        pairUrl: detail.pairUrl || null,
      },
    });
  } catch {
    // Silent – upsert failure is non-critical
  }
}
