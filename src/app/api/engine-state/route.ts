/**
 * API Route: /api/engine-state
 *
 * Returns the current PaperTradingEngine snapshot — same data the
 * WebSocket pushes to the browser, but accessible via plain HTTP.
 *
 * WHY: When the user reports an issue, asking them to visit
 *   /api/engine-state?pretty=1
 * gives us a full JSON dump of:
 *   - portfolio value, cash, PnL
 *   - open positions
 *   - recent signals
 *   - trade history (closed trades)
 *   - token states (which tokens have prices)
 *   - pattern buffer, living trie stats
 *   - money manager settings
 *
 * The user can copy-paste the URL output to me for debugging.
 */

import { NextRequest, NextResponse } from 'next/server'
import { getGlobalEngine } from '@/lib/use-trading-socket'

export async function GET(req: NextRequest) {
  const pretty = req.nextUrl.searchParams.get('pretty') === '1'
  try {
    const engine = getGlobalEngine()
    if (!engine) {
      return NextResponse.json(
        { error: 'Engine not initialized yet — visit the homepage first' },
        { status: 503 }
      )
    }
    const state = engine.snapshot()
    if (pretty) {
      return new NextResponse(
        JSON.stringify(state, null, 2),
        {
          headers: {
            'Content-Type': 'application/json; charset=utf-8',
            'Cache-Control': 'no-store',
          },
        }
      )
    }
    return NextResponse.json(state, {
      headers: { 'Cache-Control': 'no-store' },
    })
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || 'snapshot failed' },
      { status: 500 }
    )
  }
}
