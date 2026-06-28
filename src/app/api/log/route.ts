/**
 * API Route: /api/log (POST)
 *
 * Receives a batch of engine events from the client-side logger
 * (src/lib/client-logger.ts) and persists them to the server log file
 * via server-logger.ts.
 *
 * Body shape:
 *   { events: [{ ts, type, msg, data? }, ...] }
 *
 * Also supports GET (same as /api/logs — returns recent entries).
 */

import { NextRequest, NextResponse } from 'next/server'
import { logEngineEvent, readEngineLog, getLogStats } from '@/lib/server-logger'

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const events = Array.isArray(body?.events) ? body.events : []
    if (events.length === 0) {
      return NextResponse.json({ ok: true, written: 0 })
    }
    // Cap batch size to prevent abuse
    const capped = events.slice(0, 200)
    for (const e of capped) {
      if (!e || typeof e !== 'object') continue
      const type = String(e.type || 'info')
      const msg = String(e.msg || '').slice(0, 500)
      const data = e.data && typeof e.data === 'object' ? e.data : undefined
      await logEngineEvent(type as any, msg, data)
    }
    return NextResponse.json({ ok: true, written: capped.length })
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || 'log POST failed' },
      { status: 500 }
    )
  }
}

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams
  const lines = Math.min(parseInt(sp.get('lines') || '200', 10), 1000)
  const filter = sp.get('filter') || undefined
  const statsOnly = sp.get('stats') === '1'

  if (statsOnly) {
    const stats = await getLogStats()
    return NextResponse.json({ stats })
  }

  const entries = await readEngineLog(lines, filter)
  const stats = await getLogStats()
  return NextResponse.json({
    entries,
    stats,
    count: entries.length,
  })
}
