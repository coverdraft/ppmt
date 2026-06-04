import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/ohlcv
 *
 * Returns OHLCV candle data from the database.
 * If no candles exist for the requested token, fetches ON-DEMAND
 * from CoinGecko (free API) and stores them for future requests.
 *
 * KEY FIX: CoinGecko only provides specific timeframes:
 *   - 1 day  → 30 min candles
 *   - 7+ days → 4 hour candles
 * When user requests "1h" but we only have "30m" or "4h",
 * we automatically fall back to the closest available timeframe.
 *
 * Data sources: CoinGecko (primary) + DexPaprika (fallback) + DexScreener (supplementary)
 */

// Timeframe to CoinGecko days parameter mapping
const TIMEFRAME_TO_DAYS: Record<string, number> = {
  '30m': 1,
  '1h': 7,   // Will actually get 4h candles from CoinGecko
  '4h': 7,
  '1d': 90,
};

// Default days per timeframe if not in map
const DEFAULT_DAYS: Record<string, number> = {
  '1m': 1, '3m': 1, '5m': 1, '15m': 1, '30m': 1,
  '1h': 7, '2h': 7, '4h': 7, '6h': 14, '12h': 30,
  '1d': 90, '1w': 365,
};

/**
 * Fallback timeframe order: if the requested timeframe has no candles,
 * try these in order until we find data.
 */
const TIMEFRAME_FALLBACKS: Record<string, string[]> = {
  '1m':  ['1m', '5m', '15m', '30m'],
  '3m':  ['3m', '5m', '15m', '30m'],
  '5m':  ['5m', '15m', '30m'],
  '15m': ['15m', '30m', '1h'],
  '30m': ['30m', '4h', '1h'],
  '1h':  ['1h', '4h', '30m'],     // 1h → try 4h (CoinGecko default for 7 days)
  '2h':  ['2h', '4h', '1h'],
  '4h':  ['4h', '1h', '1d'],
  '6h':  ['6h', '4h', '1d'],
  '12h': ['12h', '4h', '1d'],
  '1d':  ['1d', '4h'],
  '1w':  ['1w', '1d', '4h'],
};

export async function GET(request: NextRequest) {
  try {
    const { db } = await import('@/lib/db');

    const searchParams = request.nextUrl.searchParams;
    const tokenAddress = searchParams.get('tokenAddress') || '';
    const requestedTimeframe = searchParams.get('timeframe') || '4h';
    const limit = Math.min(parseInt(searchParams.get('limit') || '200', 10), 1000);
    const forceRefresh = searchParams.get('refresh') === 'true';
    const chain = searchParams.get('chain') || '';

    if (!tokenAddress) {
      return NextResponse.json(
        { candles: [], timeframe: requestedTimeframe, source: 'none', error: 'tokenAddress is required' },
        { status: 400 },
      );
    }

    // Resolve tokenAddress: if it looks like a CUID (DB id), look up the actual address
    let resolvedAddress = tokenAddress;
    let resolvedChain = chain || '';
    if (tokenAddress.startsWith('cl') || tokenAddress.startsWith('cm')) {
      const token = await db.token.findUnique({ where: { id: tokenAddress } });
      if (token) {
        resolvedAddress = token.address;
        resolvedChain = token.chain || resolvedChain;
      }
    }

    // Try the requested timeframe first, then fallbacks
    const fallbacks = TIMEFRAME_FALLBACKS[requestedTimeframe] || [requestedTimeframe, '4h', '1d'];

    for (const tf of fallbacks) {
      // Query candles from DB
      const candles = await db.priceCandle.findMany({
        where: { tokenAddress: resolvedAddress, timeframe: tf },
        orderBy: { timestamp: 'desc' },
        take: limit,
      });

      if (candles.length > 0 && !forceRefresh) {
        const responseCandles = candles.map(c => ({
          timestamp: c.timestamp.getTime(),
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume,
        }));
        responseCandles.reverse();

        return NextResponse.json({
          candles: responseCandles,
          timeframe: tf,
          source: 'database',
          count: responseCandles.length,
          requestedTimeframe,
          fallback: tf !== requestedTimeframe,
        });
      }
    }

    // No candles in DB — fetch on-demand from CoinGecko
    console.log(`[/api/market/ohlcv] No candles for ${resolvedAddress}, fetching on-demand...`);

    const fetchedCandles = await fetchOHLCVOnDemand(resolvedAddress, requestedTimeframe, resolvedChain, db);

    if (fetchedCandles > 0) {
      // After on-demand fetch, try all fallbacks again
      for (const tf of fallbacks) {
        const candles = await db.priceCandle.findMany({
          where: { tokenAddress: resolvedAddress, timeframe: tf },
          orderBy: { timestamp: 'desc' },
          take: limit,
        });

        if (candles.length > 0) {
          const responseCandles = candles.map(c => ({
            timestamp: c.timestamp.getTime(),
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
          }));
          responseCandles.reverse();

          return NextResponse.json({
            candles: responseCandles,
            timeframe: tf,
            source: 'coingecko_ondemand',
            count: responseCandles.length,
            requestedTimeframe,
            fallback: tf !== requestedTimeframe,
          });
        }
      }
    }

    // Last resort: find ANY candles for this token
    const anyCandles = await db.priceCandle.findMany({
      where: { tokenAddress: resolvedAddress },
      orderBy: { timestamp: 'desc' },
      take: limit,
    });

    if (anyCandles.length > 0) {
      const availableTF = anyCandles[0].timeframe;
      const candles = await db.priceCandle.findMany({
        where: { tokenAddress: resolvedAddress, timeframe: availableTF },
        orderBy: { timestamp: 'desc' },
        take: limit,
      });

      const responseCandles = candles.map(c => ({
        timestamp: c.timestamp.getTime(),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }));
      responseCandles.reverse();

      return NextResponse.json({
        candles: responseCandles,
        timeframe: availableTF,
        source: 'database_fallback',
        count: responseCandles.length,
        requestedTimeframe,
        fallback: true,
      });
    }

    return NextResponse.json({
      candles: [],
      timeframe: requestedTimeframe,
      source: 'none',
      count: 0,
    });
  } catch (error) {
    console.error('[/api/market/ohlcv] Failed:', error);
    return NextResponse.json(
      { candles: [], timeframe: '4h', source: 'none', error: error instanceof Error ? error.message : 'Failed' },
      { status: 500 },
    );
  }
}

/**
 * Fetch OHLCV data on-demand and store in DB.
 *
 * Source priority:
 * 1. Binance — ALL timeframes, REAL volume, 1200 req/min (BEST quality)
 * 2. CoinGecko — Limited timeframes (30m, 4h), needs separate volume call
 * 3. DexPaprika — DEX tokens with pair addresses
 */
async function fetchOHLCVOnDemand(
  tokenAddress: string,
  timeframe: string,
  chain: string,
  db: any,
): Promise<number> {
  let totalStored = 0;

  // Look up the token from DB
  const token = await db.token.findFirst({
    where: {
      OR: [
        { address: tokenAddress },
        { id: tokenAddress },
      ],
    },
  });

  const resolvedChain = token?.chain || chain || 'SOL';
  const tokenSymbol = token?.symbol;

  // ----------------------------------------------------------
  // STEP 1: Try Binance (BEST data quality — all timeframes + real volume)
  // ----------------------------------------------------------
  if (tokenSymbol) {
    try {
      const { binanceClient } = await import('@/lib/services/data-sources/binance-client');

      const binanceSymbol = await binanceClient.resolveSymbol(tokenSymbol);
      if (binanceSymbol) {
        // Map timeframe to Binance interval
        const TF_TO_BINANCE: Record<string, string> = {
          '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
          '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '12h': '12h', '1d': '1d', '1w': '1w',
        };

        const interval = TF_TO_BINANCE[timeframe];
        if (interval) {
          const candles = await binanceClient.getKlines(binanceSymbol, interval as any, 500);

          if (candles.length > 0) {
            for (const candle of candles) {
              try {
                await db.priceCandle.upsert({
                  where: {
                    tokenAddress_chain_timeframe_timestamp: {
                      tokenAddress,
                      chain: resolvedChain,
                      timeframe,
                      timestamp: new Date(candle.openTime),
                    },
                  },
                  create: {
                    tokenAddress,
                    chain: resolvedChain,
                    timeframe,
                    timestamp: new Date(candle.openTime),
                    open: candle.open,
                    high: candle.high,
                    low: candle.low,
                    close: candle.close,
                    volume: candle.quoteVolume, // USDT volume
                    trades: candle.trades,
                    source: 'binance',
                  },
                  update: {
                    close: candle.close,
                    high: candle.high,
                    low: candle.low,
                    volume: candle.quoteVolume,
                    trades: candle.trades,
                  },
                });
                totalStored++;
              } catch { /* skip duplicates */ }
            }
            console.log(`[/api/market/ohlcv] Binance: ${totalStored} ${timeframe} candles for ${tokenSymbol}`);
            return totalStored;
          }
        }
      }
    } catch {
      // Binance failed, try CoinGecko
    }
  }

  // ----------------------------------------------------------
  // STEP 2: Try CoinGecko (fallback for non-Binance tokens)
  // ----------------------------------------------------------
  try {
    const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');

    // Determine CoinGecko days parameter
    const days = TIMEFRAME_TO_DAYS[timeframe] || DEFAULT_DAYS[timeframe] || 7;

    // Resolve CoinGecko coin ID from token address
    let coinId: string | null = null;

    // FAST PATH: Most tokens in our DB use CoinGecko coinId as address
    const isLikelyCoinGeckoId = /^[a-z0-9-]+$/.test(tokenAddress)
      && !tokenAddress.startsWith('0x')
      && tokenAddress.length < 50;

    if (isLikelyCoinGeckoId) {
      coinId = tokenAddress;
    }

    // SLOW PATH: Contract address lookup
    if (!coinId && token?.chain) {
      try {
        coinId = await coinGeckoClient.getCoinIdFromContract(resolvedChain, tokenAddress);
      } catch { /* contract lookup failed */ }
    }

    // LAST RESORT: Search by symbol
    if (!coinId && tokenSymbol) {
      try {
        const searchResults = await coinGeckoClient.searchTokens(tokenSymbol);
        const match = searchResults?.find(c =>
          c.symbol?.toUpperCase() === tokenSymbol.toUpperCase()
        );
        if (match?.id) {
          coinId = match.id;
        }
      } catch { /* search failed */ }
    }

    if (!coinId) {
      console.log(`[/api/market/ohlcv] Could not resolve CoinGecko ID for ${tokenAddress} (${tokenSymbol})`);
      return 0;
    }

    // Fetch OHLCV data
    let ohlcv: Array<{ timestamp: number; open: number; high: number; low: number; close: number }> = [];
    try {
      ohlcv = await coinGeckoClient.getOHLCV(coinId, days);
    } catch {
      // CoinGecko OHLCV fetch failed
    }

    if (ohlcv.length > 0) {
      const cgTimeframe = coinGeckoClient.getOHLCVTimeframe(days);

      // Fetch volume data from /market_chart
      let volumeMap = new Map<number, number>();
      try {
        const chartData = await coinGeckoClient.getMarketChart(coinId, days);
        if (chartData?.total_volumes) {
          const candleMs = days === 1 ? 30 * 60 * 1000 : 4 * 60 * 60 * 1000;
          volumeMap = coinGeckoClient.buildVolumeMap(chartData.total_volumes, candleMs);
        }
      } catch {
        // Volume fetch failed
      }

      const candleMs = days === 1 ? 30 * 60 * 1000 : 4 * 60 * 60 * 1000;

      for (const candle of ohlcv) {
        try {
          const roundedTs = Math.floor(candle.timestamp / candleMs) * candleMs;
          const volume = volumeMap.get(roundedTs) || 0;

          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress,
                chain: resolvedChain,
                timeframe: cgTimeframe,
                timestamp: new Date(candle.timestamp),
              },
            },
            create: {
              tokenAddress,
              chain: resolvedChain,
              timeframe: cgTimeframe,
              timestamp: new Date(candle.timestamp),
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close,
              volume,
              source: 'coingecko',
            },
            update: {
              open: candle.open,
              high: candle.high,
              low: candle.low,
              close: candle.close,
              volume,
            },
          });
          totalStored++;
        } catch { /* skip duplicates */ }
      }

      console.log(`[/api/market/ohlcv] Stored ${totalStored} ${cgTimeframe} candles for ${tokenAddress} from CoinGecko (with volume)`);

      // If user wanted 1d candles, also fetch daily data
      if (cgTimeframe !== '1d' && (timeframe === '1d' || timeframe === '1w')) {
        try {
          const dailyOhlcv = await coinGeckoClient.getOHLCV(coinId, 90);
          for (const candle of dailyOhlcv) {
            try {
              await db.priceCandle.upsert({
                where: {
                  tokenAddress_chain_timeframe_timestamp: {
                    tokenAddress,
                    chain: resolvedChain,
                    timeframe: '1d',
                    timestamp: new Date(candle.timestamp),
                  },
                },
                create: {
                  tokenAddress,
                  chain: resolvedChain,
                  timeframe: '1d',
                  timestamp: new Date(candle.timestamp),
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: 0,
                  source: 'coingecko',
                },
                update: {
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                },
              });
              totalStored++;
            } catch { /* skip */ }
          }
        } catch { /* daily fetch failed */ }
      }
    }

    // If CoinGecko didn't work, try DexPaprika (for DEX tokens)
    if (totalStored === 0 && token?.pairAddress) {
      try {
        const { dexPaprikaClient } = await import('@/lib/services/data-sources/dexpaprika-client');
        const chainNorm = token.chain === 'SOL' ? 'solana' :
          token.chain === 'ETH' ? 'ethereum' :
            token.chain === 'BASE' ? 'base' : token.chain.toLowerCase();

        const dpOhlcv = await dexPaprikaClient.getOHLCV(
          chainNorm,
          token.pairAddress,
          timeframe,
          200,
        );

        if (dpOhlcv.length > 0) {
          for (const candle of dpOhlcv) {
            try {
              await db.priceCandle.upsert({
                where: {
                  tokenAddress_chain_timeframe_timestamp: {
                    tokenAddress,
                    chain: resolvedChain,
                    timeframe,
                    timestamp: new Date(candle.timestamp * 1000),
                  },
                },
                create: {
                  tokenAddress,
                  chain: resolvedChain,
                  timeframe,
                  timestamp: new Date(candle.timestamp * 1000),
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: candle.volume,
                  source: 'dexpaprika',
                },
                update: {
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: candle.volume,
                },
              });
              totalStored++;
            } catch { /* skip */ }
          }
          console.log(`[/api/market/ohlcv] Stored ${totalStored} ${timeframe} candles from DexPaprika`);
        }
      } catch { /* DexPaprika OHLCV not available for this token */ }
    }

  } catch (err) {
    console.warn(`[/api/market/ohlcv] On-demand fetch failed for ${tokenAddress}:`, err);
  }

  return totalStored;
}
