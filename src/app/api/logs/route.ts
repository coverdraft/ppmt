/**
 * API Route: /api/logs
 *
 * Returns recent engine log entries.
 *
 * Query params:
 *   lines=200  — number of lines to return (max 1000)
 *   filter=signal  — filter by type or message substring
 *   stats=1    — return only stats (file size, line count), no entries
 *
 * Example:
 *   /api/logs?lines=50&filter=signal
 *   /api/logs?stats=1
 */

import { NextRequest, NextResponse } from 'next/server'
import { readEngineLog, getLogStats } from '@/lib/server-logger'

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
