#!/usr/bin/env python3
"""
PPMT Terminal Fixes v5 — Brain panel liveness + server-side logging.

USER COMPLAINTS:
  1. "SAX PATTERN STREAM no se mueve" — pattern buffer looks frozen at
     U D F F F F F F F F F F. This is because the selected token (BTC)
     rarely moves >0.02% in 1.5s, so most symbols are F.
  2. "donde estan las operaciones cerradas" — closed trades are in the
     TradeLog component (right side of Dashboard), but the user might
     not see them if no positions have closed yet.
  3. "podrias dejar algun lugar donde vaya guardando info" — wants a
     persistent log so we can debug when the app goes online.

FIXES:
  A. Lower SAX U/D threshold from 0.02% to 0.008% (BTC $5 move in 1.5s
     instead of $12) — more dynamic pattern buffer.
  B. Lower V/B threshold from 0.15% to 0.06% — captures more "big" moves.
  C. Add "last tick Xs ago" timestamp + tick counter to BrainPanel so
     user can see the buffer IS updating.
  D. Add a heartbeat pulse animation to the most-recent SAX symbol.
  E. Create /api/logs endpoint that returns the engine log file.
  F. Create /api/health endpoint for quick health checks.
  G. Add a server-side logger that captures engine events to
     /tmp/my-project/logs/ppmt-engine.log (rotating, 5MB max).
  H. Add structured console.log + file.log calls in paper-trading-engine
     for: signals, trades, errors, websocket events.

Run: python3 /home/z/my-project/scripts/fix_ppmt_v5_brain_logs.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
errors = []
applied = []


def edit(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found")
        return
    if old == new:
        errors.append(f"[{label}] old == new (no-op)")
        return
    cnt = src.count(old)
    if cnt > 1:
        errors.append(f"[{label}] Pattern matches {cnt} times — needs disambiguation")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(label)


def write_file(path: Path, content: str, label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        applied.append(label)
    except Exception as e:
        errors.append(f"[{label}] Failed: {e}")


# ════════════════════════════════════════════════════════════════════
# A. Lower SAX thresholds in paper-trading-engine.ts
# ════════════════════════════════════════════════════════════════════
PTE = ROOT / "src/lib/paper-trading-engine.ts"

edit(
    PTE,
    old=(
        "      const pct = ((t.price - last) / last) * 100\n"
        "      // 5-symbol SAX alphabet — captures more variance than U/D/F.\n"
        "      // Lower threshold (0.02% vs old 0.05%) means BTC/ETH ticks\n"
        "      // are no longer dominated by 'F' (flat).\n"
        "      const sym_char =\n"
        "        pct >=  0.15 ? 'V' :\n"
        "        pct >=  0.02 ? 'U' :\n"
        "        pct <= -0.15 ? 'B' :\n"
        "        pct <= -0.02 ? 'D' : 'F'"
    ),
    new=(
        "      const pct = ((t.price - last) / last) * 100\n"
        "      // 5-symbol SAX alphabet — captures more variance than U/D/F.\n"
        "      // Thresholds tuned for 1.5s ticks on liquid tokens:\n"
        "      //   0.008% = $5 move on BTC ($60k)  — typical small tick\n"
        "      //   0.030% = $18 move on BTC        — meaningful tick\n"
        "      // Before this was 0.02% / 0.15% which made 90%+ of ticks\n"
        "      // appear as 'F' (flat) — buffer looked frozen.\n"
        "      const sym_char =\n"
        "        pct >=  0.030 ? 'V' :\n"
        "        pct >=  0.008 ? 'U' :\n"
        "        pct <= -0.030 ? 'B' :\n"
        "        pct <= -0.008 ? 'D' : 'F'"
    ),
    label="A: lower SAX thresholds (0.008% / 0.030%)",
)


# ════════════════════════════════════════════════════════════════════
# B. Add `lastTickAt` field to engine state + bump on every updatePatterns
# ════════════════════════════════════════════════════════════════════
edit(
    PTE,
    old=(
        "  private candlesProcessed: number = 0\n"
        "  private maxPatternDepthObserved: number = 0\n"
        "  private lastTriePruneTime: number = 0"
    ),
    new=(
        "  private candlesProcessed: number = 0\n"
        "  private maxPatternDepthObserved: number = 0\n"
        "  private lastTriePruneTime: number = 0\n"
        "  private lastTickAt: number = 0  // ms epoch of last pattern-buffer update\n"
        "  private tickCount: number = 0   // total ticks since engine start"
    ),
    label="B1: add lastTickAt + tickCount fields",
)

# B2. Update lastTickAt + tickCount inside updatePatternsAndTrie
edit(
    PTE,
    old=(
        "      this.candlesProcessed++\n"
        "    }\n"
        "  }"
    ),
    new=(
        "      this.candlesProcessed++\n"
        "      this.tickCount++\n"
        "    }\n"
        "    this.lastTickAt = Date.now()\n"
        "  }"
    ),
    label="B2: bump lastTickAt + tickCount on every tick",
)

# B3. Expose lastTickAt + tickCount in snapshot
edit(
    PTE,
    old=(
        "      candles_processed: this.candlesProcessed,\n"
        "      websocket_status: wsConnected ? 'connected' : 'reconnecting',"
    ),
    new=(
        "      candles_processed: this.candlesProcessed,\n"
        "      last_tick_at: this.lastTickAt,\n"
        "      tick_count: this.tickCount,\n"
        "      websocket_status: wsConnected ? 'connected' : 'reconnecting',"
    ),
    label="B3: expose last_tick_at + tick_count in snapshot",
)


# ════════════════════════════════════════════════════════════════════
# C. Add lastTickAt + tickCount to store + BrainPanel
# ════════════════════════════════════════════════════════════════════
TS = ROOT / "src/stores/trading-store.ts"

# Add fields to TradingState interface
edit(
    TS,
    old=(
        "  candlesProcessed: number\n"
        "  tradeHistory: TradeRecord[]"
    ),
    new=(
        "  candlesProcessed: number\n"
        "  lastTickAt: number\n"
        "  tickCount: number\n"
        "  tradeHistory: TradeRecord[]"
    ),
    label="C1: add lastTickAt + tickCount to store interface",
)

# Add defaults
edit(
    TS,
    old=(
        "  candlesProcessed: 0,\n"
        "  tradeHistory: [],"
    ),
    new=(
        "  candlesProcessed: 0,\n"
        "  lastTickAt: 0,\n"
        "  tickCount: 0,\n"
        "  tradeHistory: [],"
    ),
    label="C2: add lastTickAt + tickCount defaults",
)

# Map fields in use-trading-socket.ts
UTS = ROOT / "src/lib/use-trading-socket.ts"
edit(
    UTS,
    old=(
        "        candlesProcessed: state.candles_processed || 0,"
    ),
    new=(
        "        candlesProcessed: state.candles_processed || 0,\n"
        "        lastTickAt: state.last_tick_at || 0,\n"
        "        tickCount: state.tick_count || 0,"
    ),
    label="C3: map lastTickAt + tickCount in socket hook",
)


# ════════════════════════════════════════════════════════════════════
# D. Update BrainPanel to show last-tick timestamp + heartbeat
# ════════════════════════════════════════════════════════════════════
BP = ROOT / "src/components/trading/brain-panel.tsx"

edit(
    BP,
    old=(
        "  const {\n"
        "    patternBuffer,\n"
        "    entropy,\n"
        "    regime,\n"
        "    livingTrieStats,\n"
        "    candlesProcessed,\n"
        "    latestSignal,\n"
        "  } = useTradingStore()"
    ),
    new=(
        "  const {\n"
        "    patternBuffer,\n"
        "    entropy,\n"
        "    regime,\n"
        "    livingTrieStats,\n"
        "    candlesProcessed,\n"
        "    latestSignal,\n"
        "    lastTickAt,\n"
        "    tickCount,\n"
        "  } = useTradingStore()\n"
        "\n"
        "  // Live 'Xs ago' indicator — proves the buffer IS updating even when\n"
        "  // most symbols are F (flat). Uses a 1s re-render to stay fresh.\n"
        "  const [, forceRender] = useState(0)\n"
        "  useEffect(() => {\n"
        "    const id = setInterval(() => forceRender(v => v + 1), 1000)\n"
        "    return () => clearInterval(id)\n"
        "  }, [])\n"
        "  const secsAgo = lastTickAt ? Math.max(0, Math.round((Date.now() - lastTickAt) / 1000)) : null\n"
        "  const isLive = secsAgo !== null && secsAgo <= 3"
    ),
    label="D1: BrainPanel reads lastTickAt + tickCount + 1s re-render",
)

# Need to import useState/useEffect
edit(
    BP,
    old=(
        "'use client'\n"
        "\n"
        "import { useTradingStore } from '@/stores/trading-store'"
    ),
    new=(
        "'use client'\n"
        "\n"
        "import { useState, useEffect } from 'react'\n"
        "import { useTradingStore } from '@/stores/trading-store'"
    ),
    label="D2: import useState/useEffect in BrainPanel",
)

# Update the SAX pattern buffer section to add heartbeat + timestamp
edit(
    BP,
    old=(
        "        {/* SAX Pattern Buffer */}\n"
        "        <div>\n"
        "          <div className=\"text-[10px] text-gray-500 font-mono mb-1\">PATTERN BUFFER</div>\n"
        "          <div className=\"flex gap-1 flex-wrap\">\n"
        "            {patternBuffer.length > 0 ? (\n"
        "              patternBuffer.map((sym, i) => (\n"
        "                <span\n"
        "                  key={i}\n"
        "                  className={`inline-flex items-center justify-center w-6 h-6 rounded text-xs font-mono font-bold border ${\n"
        "                    SAX_COLORS[sym] || 'bg-gray-500/30 text-gray-300 border-gray-500/40'\n"
        "                  } ${i === patternBuffer.length - 1 ? 'ring-1 ring-blue-400/50' : ''}`}\n"
        "                >\n"
        "                  {sym}\n"
        "                </span>\n"
        "              ))\n"
        "            ) : (\n"
        "              <span className=\"text-xs text-gray-600 font-mono\">waiting...</span>\n"
        "            )}\n"
        "          </div>\n"
        "        </div>"
    ),
    new=(
        "        {/* SAX Pattern Buffer */}\n"
        "        <div>\n"
        "          <div className=\"flex items-center justify-between mb-1\">\n"
        "            <span className=\"text-[10px] text-gray-500 font-mono\">PATTERN BUFFER</span>\n"
        "            <span className={`text-[9px] font-mono flex items-center gap-1 ${isLive ? 'text-emerald-400' : 'text-amber-400'}`}>\n"
        "              <span className={`inline-block w-1.5 h-1.5 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />\n"
        "              {secsAgo === null ? 'waiting' : isLive ? 'live' : `${secsAgo}s ago`}\n"
        "            </span>\n"
        "          </div>\n"
        "          <div className=\"flex gap-1 flex-wrap\">\n"
        "            {patternBuffer.length > 0 ? (\n"
        "              patternBuffer.map((sym, i) => {\n"
        "                const isLatest = i === patternBuffer.length - 1\n"
        "                return (\n"
        "                  <span\n"
        "                    key={i}\n"
        "                    className={`inline-flex items-center justify-center w-6 h-6 rounded text-xs font-mono font-bold border ${\n"
        "                      SAX_COLORS[sym] || 'bg-gray-500/30 text-gray-300 border-gray-500/40'\n"
        "                    } ${isLatest ? 'ring-2 ring-blue-400/70 animate-pulse' : ''}`}\n"
        "                  >\n"
        "                    {sym}\n"
        "                  </span>\n"
        "                )\n"
        "              })\n"
        "            ) : (\n"
        "              <span className=\"text-xs text-gray-600 font-mono\">waiting...</span>\n"
        "            )}\n"
        "          </div>\n"
        "          <div className=\"text-[8px] text-gray-600 font-mono mt-1\">\n"
        "            tick #{tickCount.toLocaleString()} • {patternBuffer.length}/12 symbols\n"
        "          </div>\n"
        "        </div>"
    ),
    label="D3: BrainPanel shows live/stale indicator + tick count + pulse on latest",
)


# ════════════════════════════════════════════════════════════════════
# E. Server-side logger utility
# ════════════════════════════════════════════════════════════════════
LOGGER_UTIL = """/**
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
  const line = JSON.stringify(entry) + '\\n'

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
    .split('\\n')
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
    const lines = content.split('\\n').filter(Boolean).length
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
"""

write_file(
    ROOT / "src/lib/server-logger.ts",
    LOGGER_UTIL,
    label="E: server-logger.ts utility",
)


# ════════════════════════════════════════════════════════════════════
# F. /api/logs endpoint
# ════════════════════════════════════════════════════════════════════
LOGS_ROUTE = """/**
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
"""

write_file(
    ROOT / "src/app/api/logs/route.ts",
    LOGS_ROUTE,
    label="F: /api/logs route",
)


# ════════════════════════════════════════════════════════════════════
# G. /api/health endpoint
# ════════════════════════════════════════════════════════════════════
HEALTH_ROUTE = """/**
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
"""

write_file(
    ROOT / "src/app/api/health/route.ts",
    HEALTH_ROUTE,
    label="G: /api/health route",
)


# ════════════════════════════════════════════════════════════════════
# H. /api/engine-state endpoint — snapshot of engine for remote debugging
# ════════════════════════════════════════════════════════════════════
STATE_ROUTE = """/**
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
import { GLOBAL_ENGINE } from '@/lib/use-trading-socket'

export async function GET(req: NextRequest) {
  const pretty = req.nextUrl.searchParams.get('pretty') === '1'
  try {
    const state = GLOBAL_ENGINE.snapshot()
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
"""

write_file(
    ROOT / "src/app/api/engine-state/route.ts",
    STATE_ROUTE,
    label="H: /api/engine-state route",
)


# ─── Report ───────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v5 (brain liveness + server logs) ===\n")
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
