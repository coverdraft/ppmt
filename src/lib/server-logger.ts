/**
 * Server-side logger — persists engine events to a rotating log file.
 *
 * WHY: When the terminal goes live, we need a persistent record of:
 *   - signals generated (direction, symbol, confidence, EV)
 *   - trades opened/closed (entry, exit, PnL, reason)
 *   - errors (WebSocket disconnects, API failures, etc.)
 *   - kill switch / circuit breaker activations
 *
 * Without this, debugging live issues requires the user to copy-paste
 * browser console output, which is unreliable.
 *
 * LOG LOCATION: /tmp/ppmt-engine.log (rotated at 5MB, max 3 files)
 *
 * USAGE (server-side only — API routes):
 *   import { logEngineEvent } from '@/lib/server-logger'
 *   logEngineEvent('signal', { direction: 'LONG', symbol: 'BTC/USDT', ... })
 */

import { promises as fs } from 'fs'
import path from 'path'

const LOG_DIR = process.env.PPMT_LOG_DIR || '/tmp'
const LOG_FILE = path.join(LOG_DIR, 'ppmt-engine.log')
const MAX_FILE_SIZE = 5 * 1024 * 1024  // 5 MB
const MAX_FILES = 3

export type EngineEventType =
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

interface LogEntry {
  ts: string  // ISO timestamp
  type: EngineEventType
  msg: string
  data?: Record<string, unknown>
}

let writeQueue: Promise<void> = Promise.resolve()

async function rotateIfNeeded() {
  try {
    const stat = await fs.stat(LOG_FILE).catch(() => null)
    if (!stat || stat.size < MAX_FILE_SIZE) return

    // Rotate: .2 -> .3 (delete), .1 -> .2, current -> .1
    for (let i = MAX_FILES - 1; i >= 1; i--) {
      const older = `${LOG_FILE}.${i}`
      const newer = `${LOG_FILE}.${i - 1 || ''}`.replace(/\.$/, '')
      await fs.rename(newer, older).catch(() => {})
    }
    await fs.rename(LOG_FILE, `${LOG_FILE}.1`).catch(() => {})
  } catch (e) {
    // Best-effort rotation
  }
}

export function logEngineEvent(
  type: EngineEventType,
  msg: string,
  data?: Record<string, unknown>,
) {
  const entry: LogEntry = {
    ts: new Date().toISOString(),
    type,
    msg,
    ...(data && Object.keys(data).length > 0 ? { data } : {}),
  }
  const line = JSON.stringify(entry) + '\n'

  // Serialize writes to avoid interleaving
  writeQueue = writeQueue.then(async () => {
    try {
      await rotateIfNeeded()
      await fs.appendFile(LOG_FILE, line, 'utf8')
    } catch (e) {
      // If we can't write to the log file, fall back to console.error
      console.error('[server-logger] write failed:', e)
    }
  })
  return writeQueue
}

export async function readEngineLog(lines: number = 200, filter?: string): Promise<LogEntry[]> {
  let content: string
  try {
    content = await fs.readFile(LOG_FILE, 'utf8')
  } catch {
    return []
  }
  let entries: LogEntry[] = content
    .split('\n')
    .filter(Boolean)
    .map(line => {
      try { return JSON.parse(line) as LogEntry }
      catch { return null }
    })
    .filter((e): e is LogEntry => e !== null)

  if (filter) {
    entries = entries.filter(e =>
      e.type.includes(filter) || e.msg.toLowerCase().includes(filter.toLowerCase())
    )
  }
  return entries.slice(-lines)
}

export async function getLogStats() {
  try {
    const stat = await fs.stat(LOG_FILE).catch(() => null)
    if (!stat) return { exists: false, size: 0, lines: 0 }
    const content = await fs.readFile(LOG_FILE, 'utf8')
    const lines = content.split('\n').filter(Boolean).length
    return {
      exists: true,
      size: stat.size,
      size_mb: Math.round((stat.size / 1024 / 1024) * 100) / 100,
      lines,
      modified: stat.mtime.toISOString(),
    }
  } catch {
    return { exists: false, size: 0, lines: 0 }
  }
}
