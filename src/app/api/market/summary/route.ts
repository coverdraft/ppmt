import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

// In-memory cache for market summary (survives between requests)
let cachedSummary: any = null;
let cachedAt = 0;
const CACHE_TTL = 60_000; // 60 seconds

/**
 * GET /api/market/summary
 *
 * Returns market summary with robust fallback chain:
 *   1. Cached in-memory (if < 60s old)
 *   2. CoinGecko via coinGeckoClient (rate-limited, cached)
 *   3. Database aggregation fallback
 */
export async function GET() {
  // Return in-memory cache if fresh
  if (cachedSummary && Date.now() - cachedAt < CACHE_TTL) {
    return NextResponse.json({
      data: cachedSummary,
      error: null,
      source: 'cache',
    });
  }

  try {
    // Try CoinGecko via the rate-limited client (no raw fetch)
    const summary = await fetchFromCoinGecko();

    if (summary && summary.btcPrice > 0) {
      cachedSummary = summary;
      cachedAt = Date.now();
      return NextResponse.json({
        data: summary,
        error: null,
        source: 'live',
      });
    }
  } catch (err) {
    console.warn('[/api/market/summary] CoinGecko failed, using DB fallback:', err);
  }

  // Fallback: aggregate from our database
  try {
    const dbSummary = await fetchFromDatabase();

    if (dbSummary) {
      // Cache even fallback data for 30s
      if (!cachedSummary) {
        cachedSummary = dbSummary;
        cachedAt = Date.now() - CACHE_TTL + 30_000; // Cache for 30s only
      }
      return NextResponse.json({
        data: dbSummary,
        error: null,
        source: 'database',
      });
    }
  } catch (err) {
    console.warn('[/api/market/summary] DB fallback failed:', err);
  }

  // Last resort: return stale cache or empty
  if (cachedSummary) {
    return NextResponse.json({
      data: cachedSummary,
      error: 'Using stale cache (API unavailable)',
      source: 'stale_cache',
    });
  }

  return NextResponse.json(
    { data: null, error: 'Market data unavailable', source: 'fallback' },
    { status: 503 }
  );
}

// ============================================================
// CoinGecko via coinGeckoClient (rate-limited, cached)
// ============================================================

async function fetchFromCoinGecko() {
  try {
    const { coinGeckoClient } = await import('@/lib/services/data-sources/coingecko-client');

    // Fetch global data and top tokens sequentially (respects rate limit)
    const globalData = await coinGeckoClient.getMarketData();
    const topTokens = await coinGeckoClient.getTopTokens(3);

    let btcPrice = 0, ethPrice = 0, solPrice = 0;
    if (topTokens && topTokens.length > 0) {
      for (const token of topTokens) {
        if (token.coinId === 'bitcoin' || token.symbol === 'BTC') btcPrice = token.priceUsd ?? 0;
        if (token.coinId === 'ethereum' || token.symbol === 'ETH') ethPrice = token.priceUsd ?? 0;
        if (token.coinId === 'solana' || token.symbol === 'SOL') solPrice = token.priceUsd ?? 0;
      }
    }

    return {
      btcPrice,
      ethPrice,
      solPrice,
      totalMarketCap: globalData?.total_market_cap?.usd ?? 0,
      totalVolume24h: globalData?.total_volume?.usd ?? 0,
      btcDominance: globalData?.market_cap_percentage?.btc ?? 0,
      ethDominance: globalData?.market_cap_percentage?.eth ?? 0,
      fearGreedIndex: 50,
      lastUpdated: Date.now(),
    };
  } catch {
    return null;
  }
}

// ============================================================
// Database fallback
// ============================================================

async function fetchFromDatabase() {
  // Get BTC, ETH, SOL from our database
  const btc = await db.token.findFirst({
    where: { symbol: 'BTC' },
    select: { priceUsd: true, marketCap: true },
  });
  const eth = await db.token.findFirst({
    where: { symbol: 'ETH' },
    select: { priceUsd: true, marketCap: true },
  });
  const sol = await db.token.findFirst({
    where: { symbol: 'SOL' },
    select: { priceUsd: true, marketCap: true },
  });

  const totalMarketCap = await db.token.aggregate({
    _sum: { marketCap: true },
  });

  const totalVolume = await db.token.aggregate({
    _sum: { volume24h: true },
  });

  const hasAnyData = (btc?.priceUsd ?? 0) > 0;

  if (!hasAnyData) return null;

  return {
    btcPrice: btc?.priceUsd ?? 0,
    ethPrice: eth?.priceUsd ?? 0,
    solPrice: sol?.priceUsd ?? 0,
    totalMarketCap: totalMarketCap._sum.marketCap ?? 0,
    totalVolume24h: totalVolume._sum.volume24h ?? 0,
    btcDominance: btc?.marketCap && totalMarketCap._sum.marketCap
      ? (btc.marketCap / totalMarketCap._sum.marketCap) * 100
      : 0,
    ethDominance: eth?.marketCap && totalMarketCap._sum.marketCap
      ? (eth.marketCap / totalMarketCap._sum.marketCap) * 100
      : 0,
    fearGreedIndex: 50,
    lastUpdated: Date.now(),
  };
}
