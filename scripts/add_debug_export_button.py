#!/usr/bin/env python3
"""
PPMT Patch v8 — Add DEBUG EXPORT button to header.

Adds a button next to KILL that copies a full engine state snapshot to clipboard
so the user can paste it back in the chat for AI analysis.

The snapshot includes:
  - Engine status (isRunning, autoMode, isConnected, websocketStatus, tickCount)
  - strategies_perf (per-strategy P&L, win rate, open positions)
  - All open positions (symbol, direction, entry, SL, TP, P&L, strategy)
  - Last 30 closed trades (for win-rate and timing analysis)
  - Money manager settings (risk%, max positions, etc.)
  - Circuit breakers state
  - Active tokens count
  - Timestamp + session metadata

The button copies the snapshot as a fenced markdown code block so when the user
pastes it back in the chat, it's clearly delimited.

Files modified:
  1. src/components/trading/header.tsx — add EXPORT button + exportDebugSnapshot fn

Run: python3 /home/z/my-project/scripts/add_debug_export_button.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
HEADER = ROOT / "src/components/trading/header.tsx"

if not HEADER.exists():
    print(f"ERROR: header not found at {HEADER}")
    sys.exit(1)

src = HEADER.read_text()

# ─── 1. Add ClipboardCopy + Download icons to imports ─────────────────
OLD_IMPORTS = """import {
  Power,
  PowerOff,
  Skull,
  Wifi,
  WifiOff,
  Zap,
  Activity,
} from 'lucide-react'"""

NEW_IMPORTS = """import {
  Power,
  PowerOff,
  Skull,
  Wifi,
  WifiOff,
  Zap,
  Activity,
  ClipboardCopy,
  Download,
} from 'lucide-react'"""

if OLD_IMPORTS not in src:
    print("ERROR: imports block not found")
    sys.exit(1)
src = src.replace(OLD_IMPORTS, NEW_IMPORTS, 1)
print("+ imports: added ClipboardCopy, Download")

# ─── 2. Pull more state from the store ────────────────────────────────
OLD_DESTRUCT = """  const {
    isConnected,
    engineMode,
    isRunning,
    autoMode,
    symbol,
    timeframe,
    currentPrice,
    killSwitchActive,
  } = useTradingStore()"""

NEW_DESTRUCT = """  const {
    isConnected,
    engineMode,
    isRunning,
    autoMode,
    symbol,
    timeframe,
    currentPrice,
    killSwitchActive,
    // debug export fields
    positions,
    tradeHistory,
    strategies_perf,
    websocketStatus,
    tickCount,
    candlesProcessed,
    lastTickAt,
    moneyManager,
    circuitBreakers,
    activeTokens,
    selectedToken,
    kellyPercent,
    suggestedPositionSize,
  } = useTradingStore()"""

if OLD_DESTRUCT not in src:
    print("ERROR: destructuring block not found")
    sys.exit(1)
src = src.replace(OLD_DESTRUCT, NEW_DESTRUCT, 1)
print("+ store: pulled debug fields into header")

# ─── 3. Add exportDebugSnapshot function + button ─────────────────────
# Insert the function before the component return, and the button before </div> closing right section.

OLD_CLOSING = """        <Button
          size="sm"
          variant="destructive"
          onClick={onKillSwitch}
          className="h-7 text-xs font-mono gap-1 bg-red-900 hover:bg-red-800"
          disabled={killSwitchActive || !isConnected}
        >
          <Skull className="w-3 h-3" />
          KILL
        </Button>
      </div>
    </header>
  )
}"""

NEW_CLOSING = """        <Button
          size="sm"
          variant="destructive"
          onClick={onKillSwitch}
          className="h-7 text-xs font-mono gap-1 bg-red-900 hover:bg-red-800"
          disabled={killSwitchActive || !isConnected}
        >
          <Skull className="w-3 h-3" />
          KILL
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() => exportDebugSnapshot()}
          className="h-7 text-xs font-mono gap-1 bg-[#1e2a3d] hover:bg-[#2a3a5d] border-[#3a4a6d] text-gray-300"
          title="Copy full engine snapshot to clipboard for AI debugging"
        >
          <ClipboardCopy className="w-3 h-3" />
          EXPORT
        </Button>
      </div>
    </header>
  )

  /**
   * Build a full engine-state snapshot and copy it to the clipboard as a
   * fenced markdown code block. The user can paste this back in chat and
   * the AI gets a complete, parseable view of what the engine is doing.
   */
  function exportDebugSnapshot() {
    const now = new Date()
    const iso = now.toISOString()
    const ts = now.toLocaleString('es-ES', { hour12: false })

    // Trim tradeHistory to last 30 closed trades
    const recentTrades = (tradeHistory || [])
      .slice(-30)
      .map(t => ({
        symbol: t.symbol,
        dir: t.direction,
        status: t.status,
        entry: +t.entry_price?.toFixed(4),
        close: t.close_price ? +t.close_price.toFixed(4) : null,
        reason: t.close_reason || null,
        pnl_pct: +t.pnl_pct?.toFixed(2),
        pnl_usdt: +t.pnl_usdt?.toFixed(2),
        size_usdt: +t.size_usdt?.toFixed(2),
        opened: t.entry_time,
        closed: t.closed_at || null,
      }))

    // Compact positions (only the fields that matter for debugging)
    const openPos = (positions || []).filter(p => p.status === 'OPEN').map(p => ({
      symbol: p.symbol,
      dir: p.direction,
      strat: p.strategy || '?',
      entry: +p.entry_price?.toFixed(4),
      sl: p.current_sl ? +p.current_sl.toFixed(4) : null,
      tp: p.current_tp ? +p.current_tp.toFixed(4) : null,
      cat_sl: p.catastrophic_sl ? +p.catastrophic_sl.toFixed(4) : null,
      size: +p.size_usdt?.toFixed(2),
      pnl_pct: +p.pnl_pct?.toFixed(2),
      pnl_usdt: +p.pnl_usdt?.toFixed(2),
      age_min: p.entry_time
        ? Math.round((Date.now() - new Date(p.entry_time).getTime()) / 60000)
        : null,
    }))

    const snapshot = {
      meta: {
        exported_at: iso,
        exported_at_local: ts,
        engine_mode: engineMode,
        is_running: isRunning,
        auto_mode: autoMode,
        is_connected: isConnected,
        websocket_status: websocketStatus,
        tick_count: tickCount,
        candles_processed: candlesProcessed,
        last_tick_at: lastTickAt
          ? new Date(lastTickAt).toISOString()
          : null,
        seconds_since_last_tick: lastTickAt
          ? Math.round((Date.now() - lastTickAt) / 1000)
          : null,
        selected_token: selectedToken,
        active_tokens_count: (activeTokens || []).length,
        kelly_pct: +kellyPercent?.toFixed(2),
        suggested_position_size_usdt: +suggestedPositionSize?.toFixed(2),
      },
      strategies_perf,
      open_positions: openPos,
      open_positions_count: openPos.length,
      recent_closed_trades: recentTrades,
      trade_history_total: (tradeHistory || []).length,
      money_manager: moneyManager,
      circuit_breakers: circuitBreakers,
    }

    const json = JSON.stringify(snapshot, null, 2)
    const markdown = `## PPMT Engine Snapshot — ${ts}\n\n\`\`\`json\n${json}\n\`\`\``

    // Copy to clipboard
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(markdown).then(
        () => {
          console.log('%c[PPMT EXPORT] Snapshot copied to clipboard', 'color:#4ade80;font-weight:bold')
          console.log(`%c${json}`, 'color:#94a3b8')
          alert(`✅ Snapshot copiado al portapapeles (${(markdown.length / 1024).toFixed(1)} KB)\n\nPegalo en el chat para análisis.`)
        },
        (err) => {
          console.error('[PPMT EXPORT] Clipboard failed:', err)
          // Fallback: open in new window
          const w = window.open('', '_blank')
          if (w) {
            w.document.write(`<pre style="white-space:pre-wrap;font-family:monospace;font-size:11px;padding:16px">${markdown.replace(/</g, '&lt;')}</pre>`)
            w.document.title = 'PPMT Snapshot'
          } else {
            alert('No se pudo copiar ni abrir ventana. Abrí la consola (F12) y usá: window.__ppmtSnapshot')
          }
        }
      )
    } else {
      // Very old browser fallback
      console.log(markdown)
      alert('Clipboard API no disponible. El snapshot se imprimió en la consola (F12).')
    }

    // Also expose globally for manual copy
    ;(window as any).__ppmtSnapshot = markdown
    ;(window as any).__ppmtSnapshotJson = snapshot
  }
}"""

if OLD_CLOSING not in src:
    print("ERROR: closing block not found")
    sys.exit(1)
src = src.replace(OLD_CLOSING, NEW_CLOSING, 1)
print("+ header: added EXPORT button + exportDebugSnapshot function")

# ─── Write back ───────────────────────────────────────────────────────
HEADER.write_text(src)
print()
print("=" * 60)
print("  v8 DEBUG EXPORT — Applied successfully")
print("=" * 60)
print()
print(f"Modified: {HEADER.relative_to(ROOT)}")
print()
print("What changed:")
print("  + Header now pulls extra state from store (positions, tradeHistory,")
print("    strategies_perf, websocketStatus, moneyManager, circuitBreakers)")
print("  + Added EXPORT button between KILL and end of header")
print("  + exportDebugSnapshot() builds JSON snapshot, copies to clipboard")
print("    as markdown code block, exposes window.__ppmtSnapshot for fallback")
print()
print("Snapshot contents:")
print("  - meta: timestamps, engine status, tick stats, WS status")
print("  - strategies_perf: per-strategy A/B/C/D performance")
print("  - open_positions: live positions with SL/TP/P&L/age")
print("  - recent_closed_trades: last 30 closed trades")
print("  - money_manager + circuit_breakers settings")
print()
print("On your Mac:")
print("  1. git pull origin terminal-web")
print("  2. kill -9 $(lsof -ti :3000) 2>/dev/null; sleep 1; npm run dev")
print("  3. Click EXPORT button in header → snapshot copied to clipboard")
print("  4. Paste in chat — AI gets full engine state for analysis")
