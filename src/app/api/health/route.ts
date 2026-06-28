/**
 * API Route: /api/health
 *
 * Quick health check — useful for uptime monitors and for the user
 * to verify the server is alive without loading the full page.
 *
 * Returns:
 *   { ok: true, ts: '...', uptime_s: 123, log: {...} }
 */

import { NextResponse } from 'next/server'
import { getLogStats } from '@/lib/server-logger'

const startedAt = Date.now()

export async function GET() {
  const logStats = await getLogStats()
  return NextResponse.json({
    ok: true,
    ts: new Date().toISOString(),
    uptime_s: Math.round((Date.now() - startedAt) / 1000),
    log: logStats,
  })
}
