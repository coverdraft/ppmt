/**
 * API Route: /api/kraken/ticker
 *
 * Server-side proxy for Kraken's /0/public/Ticker endpoint.
 * Same reason as /api/coingecko/markets: avoid browser CORS blocking.
 *
 * Caching: 30s in-memory cache.
 */

import { NextRequest, NextResponse } from 'next/server'

const KRAKEN_URL = 'https://api.kraken.com/0/public/Ticker'

let cache: { data: any | null; ts: number; key: string } = { data: null, ts: 0, key: '' }
const CACHE_TTL_MS = 30_000

export async function GET(req: NextRequest) {
  const pair = req.nextUrl.searchParams.get('pair') || ''
  if (!pair) {
    return NextResponse.json({ error: 'Missing pair param' }, { status: 400 })
  }

  const now = Date.now()
  if (cache.data && now - cache.ts < CACHE_TTL_MS && cache.key === pair) {
    return NextResponse.json(cache.data, {
      headers: { 'Cache-Control': 'public, max-age=30, s-maxage=30' },
    })
  }

  try {
    const url = `${KRAKEN_URL}?pair=${encodeURIComponent(pair)}`
    const resp = await fetch(url, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'PPMT-Terminal/1.0',
      },
    })

    if (!resp.ok) {
      return NextResponse.json(
        { error: `Kraken HTTP ${resp.status}` },
        { status: resp.status }
      )
    }

    const json = await resp.json()
    cache = { data: json, ts: now, key: pair }

    return NextResponse.json(json, {
      headers: { 'Cache-Control': 'public, max-age=30, s-maxage=30' },
    })
  } catch (e: any) {
    console.error('[api/kraken] fetch failed:', e?.message || e)
    return NextResponse.json(
      { error: 'Kraken fetch failed: ' + (e?.message || 'unknown') },
      { status: 502 }
    )
  }
}
