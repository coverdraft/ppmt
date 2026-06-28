/**
 * Client-side logger — forwards engine events to the server log file.
 *
 * WHY: The PaperTradingEngine runs in the browser. Its console.log calls
 * only go to the browser DevTools — they're lost when the user closes
 * the tab. For live debugging we need a persistent server-side record.
 *
 * HOW: This module batches events and POSTs them to /api/log every 5s
 * (or when the batch hits 50 events). The server writes them to
 * /tmp/ppmt-engine.log via server-logger.ts.
 *
 * USAGE (browser only):
 *   import { logClient } from '@/lib/client-logger'
 *   logClient.signal('LONG', { symbol: 'BTC/USDT', confidence: 0.95 })
 *   logClient.error('WebSocket disconnect', { code: 1006 })
 */

'use client'

type ClientEventType =
  | 'signal'
  | 'trade_open'
  | 'trade_close'
  | 'error'
  | 'ws_connect'
  | 'ws_disconnect'
  | 'kill_switch'
  | 'circuit_breaker'
  | 'auto_trade_skipped'
  | 'info'

interface QueuedEvent {
  ts: string
  type: ClientEventType
  msg: string
  data?: Record<string, unknown>
}

const BATCH_SIZE = 50
const FLUSH_INTERVAL_MS = 5000

let queue: QueuedEvent[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null
let endpoint: string | null = null

function ensureFlushTimer() {
  if (flushTimer) return
  if (typeof window === 'undefined') return
  flushTimer = setInterval(flush, FLUSH_INTERVAL_MS)
  window.addEventListener('beforeunload', flush)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}

async function flush() {
  if (queue.length === 0) return
  if (!endpoint) {
    if (typeof window !== 'undefined') {
      endpoint = `${window.location.origin}/api/log`
    } else {
      return
    }
  }
  const batch = queue.splice(0, queue.length)
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([JSON.stringify({ events: batch })], {
        type: 'application/json',
      })
      navigator.sendBeacon(endpoint, blob)
    } else {
      await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      })
    }
  } catch {
    queue.unshift(...batch)
    if (queue.length > 200) queue = queue.slice(-200)
  }
}

function enqueue(type: ClientEventType, msg: string, data?: Record<string, unknown>) {
  queue.push({
    ts: new Date().toISOString(),
    type,
    msg,
    ...(data && Object.keys(data).length > 0 ? { data } : {}),
  })
  if (queue.length >= BATCH_SIZE) flush()
  ensureFlushTimer()
}

export const logClient = {
  signal: (direction: string, data: Record<string, unknown>) =>
    enqueue('signal', `Signal: ${direction} ${data.symbol || ''}`, data),

  tradeOpen: (symbol: string, data: Record<string, unknown>) =>
    enqueue('trade_open', `OPEN ${symbol}`, data),

  tradeClose: (symbol: string, data: Record<string, unknown>) =>
    enqueue('trade_close', `CLOSE ${symbol}`, data),

  error: (msg: string, data?: Record<string, unknown>) =>
    enqueue('error', msg, data),

  wsConnect: (info: string) =>
    enqueue('ws_connect', info),

  wsDisconnect: (info: string) =>
    enqueue('ws_disconnect', info),

  killSwitch: (info: string, data?: Record<string, unknown>) =>
    enqueue('kill_switch', info, data),

  circuitBreaker: (name: string, data: Record<string, unknown>) =>
    enqueue('circuit_breaker', `Circuit breaker: ${name}`, data),

  autoTradeSkipped: (reason: string, data?: Record<string, unknown>) =>
    enqueue('auto_trade_skipped', reason, data),

  info: (msg: string, data?: Record<string, unknown>) =>
    enqueue('info', msg, data),

  flush,
}
