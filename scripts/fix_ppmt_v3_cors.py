#!/usr/bin/env python3
"""
PPMT Terminal Fixes v3 — CoinGecko CORS proxy + client fetch fix.

CRITICAL FIX for the CORS error blocking all CoinGecko data:
  "Ensure CORS response header values are valid
   ... Access-Control-Allow-Origin  Missing Header"

Root cause:
  The browser blocks direct fetch() calls to api.coingecko.com because
  CoinGecko's public API does NOT send the `Access-Control-Allow-Origin`
  header. Without that header, the browser refuses to expose the response
  to JavaScript. The result: the price feed's CoinGecko poll fails on
  every cycle, the engine never gets 24h change% / volume for any token,
  and the auto-trader has no candidates to pick from → "no operations".

Fix:
  1. Create a server-side Next.js API route at /api/coingecko/markets
     that proxies the request. Server-side fetches have no CORS
     restriction, so they can hit api.coingecko.com directly.
     Added 30s in-memory cache to avoid CoinGecko rate-limiting.
  2. Modify live-price-feed.ts to fetch from /api/coingecko/markets
     (same origin, no CORS issue) instead of api.coingecko.com directly.

Run:  python3 /home/z/my-project/scripts/fix_ppmt_v3_cors.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
errors = []
applied = []


def write_file(path: Path, content: str, label: str):
    """Write a new file (creating parent dirs if needed)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        applied.append(f"[{label}] OK ({path.relative_to(ROOT)})")
    except Exception as e:
        errors.append(f"[{label}] Failed to write {path}: {e}")


def edit_file(path: Path, old: str, new: str, label: str):
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found in {path}")
        return
    if old == new:
        errors.append(f"[{label}] old == new (no-op)")
        return
    count = src.count(old)
    if count > 1:
        errors.append(f"[{label}] Pattern matches {count} times — needs disambiguation")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.relative_to(ROOT)})")


# ─── 1. Create server-side proxy API route ─────────────────────────────
PROXY_ROUTE = """/**
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
"""
write_file(
    ROOT / "src/app/api/coingecko/markets/route.ts",
    PROXY_ROUTE,
    label="1: proxy API route /api/coingecko/markets",
)


# ─── 2. live-price-feed.ts: route CoinGecko through our proxy ──────────
# 2a. Update the URL constant
edit_file(
    ROOT / "src/lib/live-price-feed.ts",
    old="const COINGECKO_MARKETS_URL = 'https://api.coingecko.com/api/v3/coins/markets'",
    new="""// Use our own Next.js API route as a proxy. The browser cannot call
// api.coingecko.com directly because CoinGecko does not send
// Access-Control-Allow-Origin (CORS). The proxy route runs server-side
// where CORS does not apply, and caches the response for 30s.
const COINGECKO_MARKETS_URL = '/api/coingecko/markets'""",
    label="2a: route CoinGecko URL through proxy",
)

# 2b. Remove the absolute-URL-only fetch options that don't apply to
#     same-origin requests (Accept header is fine to keep, but let's
#     simplify and let the proxy handle it).
edit_file(
    ROOT / "src/lib/live-price-feed.ts",
    old="""      const url = `${COINGECKO_MARKETS_URL}?vs_currency=usd&ids=${encodeURIComponent(idsParam)}&order=market_cap_desc&per_page=250&page=1&sparkline=false&price_change_percentage=24h`
      const resp = await fetch(url, {
        headers: { 'Accept': 'application/json' },
      })""",
    new="""      const url = `${COINGECKO_MARKETS_URL}?ids=${encodeURIComponent(idsParam)}`
      // Same-origin request to our /api/coingecko proxy — no CORS issue.
      const resp = await fetch(url, {
        headers: { 'Accept': 'application/json' },
      })""",
    label="2b: simplify fetch URL (proxy handles the rest)",
)


# ─── 3. Also fix Kraken CORS (it might be blocked too) ─────────────────
# Kraken's api.kraken.com also doesn't always send CORS headers reliably
# from Spain. Add a proxy for it too.
KRAKEN_PROXY = """/**
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
"""
write_file(
    ROOT / "src/app/api/kraken/ticker/route.ts",
    KRAKEN_PROXY,
    label="3: proxy API route /api/kraken/ticker",
)

edit_file(
    ROOT / "src/lib/live-price-feed.ts",
    old="const KRAKEN_TICKER_URL = 'https://api.kraken.com/0/public/Ticker'",
    new="""// Use our own Next.js API route as a proxy (same reason as CoinGecko).
const KRAKEN_TICKER_URL = '/api/kraken/ticker'""",
    label="3b: route Kraken URL through proxy",
)


# ─── Report ─────────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v3 (CORS proxy) ===\n")
if applied:
    print(f"Applied {len(applied)} edits:")
    for line in applied:
        print(f"  + {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  - {line}")
    sys.exit(1)
print("\nAll edits applied successfully.")
print("\nNext: the client now fetches /api/coingecko/markets and")
print("/api/kraken/ticker (same-origin, no CORS) instead of hitting")
print("api.coingecko.com and api.kraken.com directly.")
