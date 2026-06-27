/**
 * API Route: /api/coingecko/markets
 *
 * Server-side proxy for CoinGecko's /coins/markets endpoint.
 *
 * WHY: Browsers block direct calls to api.coingecko.com because
 * CoinGecko does NOT send the `Access-Control-Allow-Origin` header
 * on its public API. The browser console shows CORS errors and the
 * fetch fails completely, leaving the engine without 24h change% /
 * volume data for all 89 tokens. Without that data the auto-trader
 * has no candidates to pick from -> "no operations found".
 *
 * This route runs server-side (Node runtime) where CORS doesn't apply.
 * The client fetches /api/coingecko/markets?ids=... instead of the
 * CoinGecko URL directly.
 *
 * Caching: 30 seconds in-memory cache to avoid hammering CoinGecko
 * (they rate-limit to ~10-30 calls/min on the free tier).
 */

import { NextRequest, NextResponse } from 'next/server'

const COINGECKO_URL = 'https://api.coingecko.com/api/v3/coins/markets'

// In-memory cache (lives for 30s, then refetches)
let cache: { data: any[] | null; ts: number } = { data: null, ts: 0 }
const CACHE_TTL_MS = 30_000

export async function GET(req: NextRequest) {
  const ids = req.nextUrl.searchParams.get('ids') || ''
  if (!ids) {
    return NextResponse.json(
      { error: 'Missing ids param' },
      { status: 400 }
    )
  }

  // Cache hit?
  const now = Date.now()
  if (cache.data && now - cache.ts < CACHE_TTL_MS) {
    // Filter cached array by requested ids to support partial requests
    const idSet = new Set(ids.split(','))
    const filtered = cache.data.filter((c: any) => idSet.has(c.id))
    return NextResponse.json(filtered, {
      headers: {
        'Cache-Control': 'public, max-age=30, s-maxage=30',
      },
    })
  }

  // Cache miss -> fetch from CoinGecko server-side
  try {
    const url = `${COINGECKO_URL}?vs_currency=usd&ids=${encodeURIComponent(ids)}&order=market_cap_desc&per_page=250&page=1&sparkline=false&price_change_percentage=24h`
    const resp = await fetch(url, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'PPMT-Terminal/1.0',
      },
    })

    if (!resp.ok) {
      const body = await resp.text().catch(() => '')
      console.error(`[api/coingecko] HTTP ${resp.status}: ${body.slice(0, 200)}`)
      return NextResponse.json(
        { error: `CoinGecko HTTP ${resp.status}` },
        { status: resp.status }
      )
    }

    const arr = await resp.json() as any[]

    // Update full cache
    cache = { data: arr, ts: now }

    return NextResponse.json(arr, {
      headers: {
        'Cache-Control': 'public, max-age=30, s-maxage=30',
      },
    })
  } catch (e: any) {
    console.error('[api/coingecko] fetch failed:', e?.message || e)
    return NextResponse.json(
      { error: 'CoinGecko fetch failed: ' + (e?.message || 'unknown') },
      { status: 502 }
    )
  }
}
