/**
 * API Route: /api/candles?symbol=BTC/USDT&interval=1m&limit=300
 *
 * Server-side proxy that fetches OHLCV candles for candlestick chart rendering.
 * Tries Coinbase first (best historical data, no CORS issues from server),
 * falls back to Kraken OHLC endpoint.
 *
 * Response shape:
 *   {
 *     symbol: "BTC/USDT",
 *     interval: "1m",
 *     source: "coinbase" | "kraken",
 *     candles: [{ time, open, high, low, close, volume }]
 *   }
 *
 * Caching: 15s in-memory per (symbol+interval) to avoid rate limits.
 */

import { NextRequest, NextResponse } from 'next/server'

// ─── Token symbol mapping (must match live-price-feed.ts) ───────────────
interface PairMeta {
  coinbase: string | null
  kraken: string | null
}

// Minimal subset — the chart modal will only fetch candles for tokens that
// the user clicks on (i.e. tokens with positions/trades). The full map
// lives in live-price-feed.ts; if a token isn't here, we try a generic
// mapping rule as a fallback.
const PAIR_MAP: Record<string, PairMeta> = {
  'BTC/USDT':   { coinbase: 'BTC-USD',    kraken: 'XXBTZUSD' },
  'ETH/USDT':   { coinbase: 'ETH-USD',    kraken: 'XETHZUSD' },
  'SOL/USDT':   { coinbase: 'SOL-USD',    kraken: 'SOLUSD' },
  'XRP/USDT':   { coinbase: 'XRP-USD',    kraken: 'XXRPZUSD' },
  'ADA/USDT':   { coinbase: 'ADA-USD',    kraken: 'ADAUSD' },
  'AVAX/USDT':  { coinbase: 'AVAX-USD',   kraken: 'AVAXUSD' },
  'DOGE/USDT':  { coinbase: 'DOGE-USD',   kraken: 'XDGUSD' },
  'DOT/USDT':   { coinbase: 'DOT-USD',    kraken: 'DOTUSD' },
  'LINK/USDT':  { coinbase: 'LINK-USD',   kraken: 'LINKUSD' },
  'MATIC/USDT': { coinbase: 'MATIC-USD',  kraken: 'MATICUSD' },
  'LTC/USDT':   { coinbase: 'LTC-USD',    kraken: 'XLTCZUSD' },
  'BCH/USDT':   { coinbase: 'BCH-USD',    kraken: 'BCHUSD' },
  'ATOM/USDT':  { coinbase: 'ATOM-USD',   kraken: 'ATOMUSD' },
  'XLM/USDT':   { coinbase: 'XLM-USD',    kraken: 'XXLMZUSD' },
  'NEAR/USDT':  { coinbase: 'NEAR-USD',   kraken: 'NEARUSD' },
  'APT/USDT':   { coinbase: 'APT-USD',    kraken: 'APTUSD' },
  'ARB/USDT':   { coinbase: 'ARB-USD',    kraken: 'ARBUSD' },
  'OP/USDT':    { coinbase: 'OP-USD',     kraken: 'OPUSD' },
  'INJ/USDT':   { coinbase: 'INJ-USD',    kraken: 'INJUSD' },
  'FIL/USDT':   { coinbase: 'FIL-USD',    kraken: 'FILUSD' },
  'AAVE/USDT':  { coinbase: 'AAVE-USD',   kraken: 'AAVEUSD' },
  'MKR/USDT':   { coinbase: 'MKR-USD',    kraken: 'MKRUSD' },
  'SUI/USDT':   { coinbase: 'SUI-USD',    kraken: 'SUIUSD' },
  'TIA/USDT':   { coinbase: 'TIA-USD',    kraken: 'TIAUSD' },
  'RUNE/USDT':  { coinbase: null,         kraken: 'RUNEUSD' },
  'FTM/USDT':   { coinbase: null,         kraken: 'FTMUSD' },
  'SEI/USDT':   { coinbase: 'SEI-USD',    kraken: 'SEIUSD' },
  'STX/USDT':   { coinbase: 'STX-USD',    kraken: null },
  'IMX/USDT':   { coinbase: 'IMX-USD',    kraken: 'IMXUSD' },
  'GRT/USDT':   { coinbase: 'GRT-USD',    kraken: 'GRTUSD' },
  'LDO/USDT':   { coinbase: 'LDO-USD',    kraken: 'LDOUSD' },
  'SAND/USDT':  { coinbase: 'SAND-USD',   kraken: 'SANDUSD' },
  'MANA/USDT':  { coinbase: 'MANA-USD',   kraken: 'MANAUSD' },
  'AXS/USDT':   { coinbase: 'AXS-USD',    kraken: 'AXSUSD' },
  'PEPE/USDT':  { coinbase: 'PEPE-USD',   kraken: null },
  'WIF/USDT':   { coinbase: 'WIF-USD',    kraken: null },
  'SHIB/USDT':  { coinbase: 'SHIB-USD',   kraken: null },
  'PYTH/USDT':  { coinbase: 'PYTH-USD',   kraken: null },
  'JTO/USDT':   { coinbase: 'JTO-USD',    kraken: null },
  'RNDR/USDT':  { coinbase: 'RNDR-USD',   kraken: null },
}

// ─── Cache ──────────────────────────────────────────────────────────────
interface CacheEntry {
  data: any
  ts: number
}
const cache = new Map<string, CacheEntry>()
const CACHE_TTL_MS = 15_000

// ─── Helpers ────────────────────────────────────────────────────────────

function intervalToCoinbaseGranularity(interval: string): number {
  // Coinbase uses seconds for granularity
  // Allowed: 60, 300, 900, 3600, 21600, 86400
  switch (interval) {
    case '1m':  return 60
    case '5m':  return 300
    case '15m': return 900
    case '1h':  return 3600
    case '6h':  return 21600
    case '1d':  return 86400
    default:    return 60
  }
}

function intervalToKrakenInterval(interval: string): number {
  // Kraken OHLC interval param: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 (minutes)
  switch (interval) {
    case '1m':  return 1
    case '5m':  return 5
    case '15m': return 15
    case '30m': return 30
    case '1h':  return 60
    case '4h':  return 240
    case '1d':  return 1440
    default:    return 1
  }
}

/**
 * Fetch candles from Coinbase Products API.
 * Endpoint: /products/{product_id}/candles?granularity={seconds}&start=...&end=...
 * Returns array of [time, low, high, open, close, volume] (oldest first),
 * time is ISO 8601 string.
 */
async function fetchCoinbaseCandles(
  pair: string,
  interval: string,
  limit: number
): Promise<{ candles: any[]; error?: string }> {
  const granularity = intervalToCoinbaseGranularity(interval)
  const end = Math.floor(Date.now() / 1000)
  // Coinbase allows max 300 candles per request — request enough time span
  const start = end - (granularity * limit)

  const url = `https://api.exchange.coinbase.com/products/${pair}/candles?granularity=${granularity}&start=${start}&end=${end}`

  try {
    const resp = await fetch(url, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'PPMT-Terminal/1.0',
      },
      // Coinbase public endpoints are cacheable
      next: { revalidate: 10 },
    })

    if (!resp.ok) {
      return { candles: [], error: `Coinbase HTTP ${resp.status}` }
    }

    const json = await resp.json()
    if (!Array.isArray(json)) {
      return { candles: [], error: 'Coinbase non-array response' }
    }

    // Coinbase returns newest-first; reverse to oldest-first
    const candles = json
      .map((row: [number, number, number, number, number, number]) => ({
        time: row[0],           // unix seconds
        open: row[3],
        high: row[2],
        low: row[1],
        close: row[4],
        volume: row[5],
      }))
      .sort((a: any, b: any) => a.time - b.time)

    return { candles }
  } catch (e: any) {
    return { candles: [], error: 'Coinbase fetch failed: ' + (e?.message || 'unknown') }
  }
}

/**
 * Fetch candles from Kraken OHLC endpoint.
 * Endpoint: /0/public/OHLC?pair={pair}&interval={minutes}&since={since_id}
 * Returns { pair: { ... }, last: <id> }
 */
async function fetchKrakenCandles(
  pair: string,
  interval: string,
  _limit: number
): Promise<{ candles: any[]; error?: string }> {
  const intervalMin = intervalToKrakenInterval(interval)
  const url = `https://api.kraken.com/0/public/OHLC?pair=${encodeURIComponent(pair)}&interval=${intervalMin}`

  try {
    const resp = await fetch(url, {
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'PPMT-Terminal/1.0',
      },
      next: { revalidate: 10 },
    })

    if (!resp.ok) {
      return { candles: [], error: `Kraken HTTP ${resp.status}` }
    }

    const json = await resp.json()
    if (json.error && json.error.length > 0) {
      return { candles: [], error: 'Kraken: ' + json.error.join('; ') }
    }

    // Kraken returns { result: { PAIRNAME: [[time, open, high, low, close, vwap, volume, count], ...] } }
    const result = json.result || {}
    // Remove the "last" key
    const pairKey = Object.keys(result).find(k => k !== 'last')
    if (!pairKey) {
      return { candles: [], error: 'Kraken: no pair in response' }
    }

    const raw = result[pairKey] || []
    const candles = raw
      .map((row: [number, string, string, string, string, string, string, number]) => ({
        time: row[0],                  // unix seconds
        open: parseFloat(row[1]),
        high: parseFloat(row[2]),
        low: parseFloat(row[3]),
        close: parseFloat(row[4]),
        volume: parseFloat(row[6]),
      }))
      .filter((c: any) => !isNaN(c.open) && !isNaN(c.close))

    return { candles }
  } catch (e: any) {
    return { candles: [], error: 'Kraken fetch failed: ' + (e?.message || 'unknown') }
  }
}

// ─── Route handler ──────────────────────────────────────────────────────

export async function GET(req: NextRequest) {
  const symbol = req.nextUrl.searchParams.get('symbol') || ''
  const interval = req.nextUrl.searchParams.get('interval') || '1m'
  const limit = Math.min(parseInt(req.nextUrl.searchParams.get('limit') || '300', 10), 300)

  if (!symbol) {
    return NextResponse.json({ error: 'Missing symbol param' }, { status: 400 })
  }

  const cacheKey = `${symbol}:${interval}:${limit}`
  const now = Date.now()
  const hit = cache.get(cacheKey)
  if (hit && now - hit.ts < CACHE_TTL_MS) {
    return NextResponse.json(hit.data, {
      headers: { 'Cache-Control': 'public, max-age=10, s-maxage=15' },
    })
  }

  const meta = PAIR_MAP[symbol]
  if (!meta) {
    // Try generic mapping: "FOO/USDT" → "FOO-USD" for Coinbase
    const generic = symbol.replace('/USDT', '-USD').replace('/', '-')
    const tryMeta: PairMeta = { coinbase: generic, kraken: null }
    const result = await fetchCoinbaseCandles(tryMeta.coinbase!, interval, limit)
    if (result.candles.length > 0) {
      const payload = {
        symbol, interval, source: 'coinbase-generic',
        candles: result.candles.slice(-limit),
      }
      cache.set(cacheKey, { data: payload, ts: now })
      return NextResponse.json(payload, {
        headers: { 'Cache-Control': 'public, max-age=10, s-maxage=15' },
      })
    }
    return NextResponse.json(
      { error: `Unknown symbol: ${symbol}. No candles available.`, candles: [] },
      { status: 404 }
    )
  }

  // Try Coinbase first (better historical data, more reliable)
  if (meta.coinbase) {
    const result = await fetchCoinbaseCandles(meta.coinbase, interval, limit)
    if (result.candles.length > 0) {
      const payload = {
        symbol, interval, source: 'coinbase',
        candles: result.candles.slice(-limit),
      }
      cache.set(cacheKey, { data: payload, ts: now })
      return NextResponse.json(payload, {
        headers: { 'Cache-Control': 'public, max-age=10, s-maxage=15' },
      })
    }
    // fall through to Kraken
  }

  // Fallback to Kraken
  if (meta.kraken) {
    const result = await fetchKrakenCandles(meta.kraken, interval, limit)
    if (result.candles.length > 0) {
      const payload = {
        symbol, interval, source: 'kraken',
        candles: result.candles.slice(-limit),
      }
      cache.set(cacheKey, { data: payload, ts: now })
      return NextResponse.json(payload, {
        headers: { 'Cache-Control': 'public, max-age=10, s-maxage=15' },
      })
    }
  }

  return NextResponse.json(
    {
      symbol, interval, source: 'none',
      candles: [],
      error: `No OHLCV source available for ${symbol}`,
    },
    { status: 404 }
  )
}
