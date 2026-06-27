/**
 * PPMT Trading Bridge — Socket.io ↔ FastAPI WebSocket bridge.
 *
 * This mini-service bridges the Next.js frontend (Socket.io)
 * with the PPMT Python V2 Server (raw WebSocket on port 8000).
 *
 * It provides:
 *  - Real-time state updates streamed from PPMT engine
 *  - Command interface (start/stop trading, kill switch)
 *  - Simulated demo mode when PPMT Python is not running
 *
 * Architecture:
 *  [Next.js Frontend] ←→ [This Service :3003] ←→ [PPMT V2 Server :8000]
 */

import { createServer } from 'http'
import { Server } from 'socket.io'
import WebSocket from 'ws'

const PORT = 3003
const PPMT_WS_URL = process.env.PPMT_WS_URL || 'ws://127.0.0.1:8000'

// ─── Types ──────────────────────────────────────────────────
interface TradingState {
  is_running: boolean
  mode: string
  started_at: number
  current_price: number
  symbol: string
  timeframe: string
  exchange: string
  pattern_buffer: string[]
  entropy: number
  regime: string
  latest_signal: any | null
  signals_history: any[]
  positions: any[]
  portfolio_value: number
  cash: number
  unrealized_pnl: number
  realized_pnl: number
  total_pnl_pct: number
  exposure_pct: number
  daily_return_pct: number
  leverage: number
  auto_mode: boolean
  circuit_breakers: any
  is_trading_allowed: boolean
  kill_switch_active: boolean
  max_drawdown_pct: number
  daily_loss_pct: number
  total_trades: number
  winning_trades: number
  win_rate: number
  max_drawdown: number
  equity_curve: number[]
  equity_timestamps: number[]
  monte_carlo: any
  living_trie_stats: any
  trade_history: any[]
  candles_processed: number
  websocket_status: string
  reconnect_count: number
}

// ─── Demo State Generator ───────────────────────────────────
// Generates realistic-looking trading data for demo mode
// when the PPMT Python engine is not connected.

class DemoEngine {
  private price: number = 172.50
  private symbol: string = 'SOL/USDT'
  private timeframe: string = '5m'
  private positions: any[] = []
  private signals: any[] = []
  private trades: any[] = []
  private equity: number[] = [1000]
  private timestamps: number[] = [Date.now() / 1000]
  private totalTrades: number = 0
  private winningTrades: number = 0
  private realizedPnl: number = 0
  private running: boolean = false
  private patterns: string[] = ['a', 'b', 'c', 'd', 'e']
  private patternIdx: number = 0
  private activePosition: any = null
  private interval: any = null

  constructor() {}

  start() {
    if (this.running) return
    this.running = true
  }

  stop() {
    this.running = false
    if (this.interval) clearInterval(this.interval)
  }

  tick(): TradingState {
    // Simulate price movement (random walk with slight drift)
    const volatility = 0.003
    const drift = (Math.random() - 0.48) * volatility
    this.price *= (1 + drift)
    this.price = Math.max(this.price * 0.95, Math.min(this.price * 1.05, this.price))

    // Advance pattern
    this.patternIdx = (this.patternIdx + 1) % this.patterns.length
    const currentPattern = this.patterns[this.patternIdx]

    // Generate signals occasionally
    if (Math.random() < 0.08 && this.signals.length < 50) {
      const direction = Math.random() > 0.5 ? 'LONG' : 'SHORT'
      const signal = {
        timestamp: new Date().toISOString(),
        direction,
        symbol: this.symbol,
        confidence: 0.55 + Math.random() * 0.35,
        ev_score: 0.7 + Math.random() * 0.5,
        pattern_path: this.patterns.slice(0, this.patternIdx + 1).join('-'),
        expected_move_pct: 0.15 + Math.random() * 0.5,
      }
      this.signals.unshift(signal)
      if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)

      // Maybe open position from signal
      if (!this.activePosition && signal.confidence > 0.6) {
        const entry = this.price
        const move = entry * (signal.expected_move_pct / 100)
        this.activePosition = {
          symbol: this.symbol,
          direction,
          status: 'ACTIVE',
          entry_price: entry,
          entry_time: new Date().toISOString(),
          size_usdt: 100,
          current_sl: direction === 'LONG' ? entry - move * 1.2 : entry + move * 1.2,
          current_tp: direction === 'LONG' ? entry + move * 2.5 : entry - move * 2.5,
          catastrophic_sl: direction === 'LONG' ? entry - move * 3.0 : entry + move * 3.0,
          pnl_pct: 0,
          pnl_usdt: 0,
        }
      }
    }

    // Check position against price
    if (this.activePosition) {
      const pos = this.activePosition
      const isLong = pos.direction === 'LONG'
      const pnlPct = isLong
        ? ((this.price - pos.entry_price) / pos.entry_price) * 100
        : ((pos.entry_price - this.price) / pos.entry_price) * 100

      pos.pnl_pct = pnlPct
      pos.pnl_usdt = pos.size_usdt * (pnlPct / 100)

      // Check SL/TP
      let closed = false
      let closeReason = ''
      if (isLong) {
        if (this.price <= pos.current_sl) { closed = true; closeReason = 'CLOSED_BY_SL' }
        if (this.price >= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      } else {
        if (this.price >= pos.current_sl) { closed = true; closeReason = 'CLOSED_BY_SL' }
        if (this.price <= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      }

      // Also close randomly sometimes (timeout/divergence)
      if (!closed && Math.random() < 0.02) {
        closed = true
        closeReason = Math.abs(pnlPct) < 0.05 ? 'CLOSED_DIVERGENCE' : 'CLOSED_BY_SL'
      }

      if (closed) {
        this.totalTrades++
        const won = pnlPct > 0
        if (won) this.winningTrades++
        this.realizedPnl += pos.pnl_usdt

        this.trades.unshift({
          ...pos,
          close_price: this.price,
          close_reason: closeReason,
          closed_at: new Date().toISOString(),
        })
        if (this.trades.length > 100) this.trades = this.trades.slice(0, 100)
        this.activePosition = null
      }
    }

    // Update equity curve
    const unrealized = this.activePosition ? this.activePosition.pnl_usdt : 0
    const totalValue = 1000 + this.realizedPnl + unrealized
    this.equity.push(totalValue)
    this.timestamps.push(Date.now() / 1000)
    if (this.equity.length > 200) {
      this.equity = this.equity.slice(-200)
      this.timestamps = this.timestamps.slice(-200)
    }

    const maxEq = Math.max(...this.equity)
    const dd = maxEq > 0 ? ((maxEq - totalValue) / maxEq) * 100 : 0

    const positions = this.activePosition ? [this.activePosition] : []

    return {
      is_running: this.running,
      mode: 'demo',
      started_at: this.timestamps[0],
      current_price: parseFloat(this.price.toFixed(4)),
      symbol: this.symbol,
      timeframe: this.timeframe,
      exchange: 'MEXC',
      pattern_buffer: this.patterns.slice(0, this.patternIdx + 1),
      entropy: parseFloat((0.3 + Math.random() * 0.6).toFixed(3)),
      regime: ['trending_up', 'trending_down', 'ranging', 'volatile'][Math.floor(Math.random() * 4)],
      latest_signal: this.signals[0] || null,
      signals_history: this.signals.slice(0, 20),
      positions,
      portfolio_value: parseFloat(totalValue.toFixed(2)),
      cash: parseFloat((totalValue - (this.activePosition ? this.activePosition.size_usdt : 0)).toFixed(2)),
      unrealized_pnl: parseFloat(unrealized.toFixed(4)),
      realized_pnl: parseFloat(this.realizedPnl.toFixed(4)),
      total_pnl_pct: parseFloat(((totalValue - 1000) / 10).toFixed(2)),
      exposure_pct: this.activePosition ? 10 : 0,
      daily_return_pct: parseFloat(((Math.random() - 0.4) * 2).toFixed(2)),
      leverage: 3,
      auto_mode: true,
      circuit_breakers: {
        max_drawdown: dd > 15,
        daily_loss: this.realizedPnl < -50,
        volatility: false,
      },
      is_trading_allowed: dd < 20,
      kill_switch_active: false,
      max_drawdown_pct: parseFloat(dd.toFixed(2)),
      daily_loss_pct: parseFloat(Math.max(0, -this.realizedPnl / 10).toFixed(2)),
      total_trades: this.totalTrades,
      winning_trades: this.winningTrades,
      win_rate: this.totalTrades > 0 ? parseFloat((this.winningTrades / this.totalTrades).toFixed(3)) : 0,
      max_drawdown: parseFloat(dd.toFixed(2)),
      equity_curve: this.equity,
      equity_timestamps: this.timestamps,
      monte_carlo: {
        risk_of_ruin: parseFloat((Math.random() * 0.05).toFixed(4)),
        probability_of_profit: parseFloat((0.65 + Math.random() * 0.25).toFixed(3)),
        p95_dd: parseFloat((8 + Math.random() * 12).toFixed(1)),
        verdict: 'PASS',
      },
      living_trie_stats: {
        pattern_count: 1200 + Math.floor(Math.random() * 300),
        max_depth: 8 + Math.floor(Math.random() * 4),
        trading_observations: 15000 + Math.floor(Math.random() * 5000),
        last_update: new Date().toISOString(),
      },
      trade_history: this.trades.slice(0, 20),
      candles_processed: this.totalTrades * 12 + Math.floor(Math.random() * 100),
      websocket_status: 'connected',
      reconnect_count: 0,
    }
  }

  killSwitch() {
    if (this.activePosition) {
      this.activePosition.pnl_usdt = this.activePosition.pnl_pct * this.activePosition.size_usdt / 100
      this.realizedPnl += this.activePosition.pnl_usdt
      this.trades.unshift({
        ...this.activePosition,
        close_price: this.price,
        close_reason: 'CLOSED_KILL_SWITCH',
        closed_at: new Date().toISOString(),
      })
      this.totalTrades++
      if (this.activePosition.pnl_pct > 0) this.winningTrades++
      this.activePosition = null
    }
    this.running = false
  }

  setSymbol(s: string) { this.symbol = s }
  setTimeframe(tf: string) { this.timeframe = tf }
}

// ─── Server ─────────────────────────────────────────────────
const httpServer = createServer()
const io = new Server(httpServer, {
  cors: { origin: '*', methods: ['GET', 'POST'] },
  pingTimeout: 60000,
  pingInterval: 25000,
})

const demo = new DemoEngine()
let ppmtWs: WebSocket | null = null
let ppmtConnected = false
let demoInterval: any = null
let useDemo = true // Start in demo mode

// ─── Connect to PPMT Python Backend ────────────────────────
function connectPPMT() {
  try {
    ppmtWs = new WebSocket(`${PPMT_WS_URL}/ws/sol`)

    ppmtWs.on('open', () => {
      console.log('[PPMT] Connected to Python V2 Server')
      ppmtConnected = true
      useDemo = false
      // Stop demo if it was running
      if (demoInterval) {
        clearInterval(demoInterval)
        demoInterval = null
      }
      io.emit('engine-status', { connected: true, mode: 'live' })
    })

    ppmtWs.on('message', (data: WebSocket.Data) => {
      try {
        const msg = JSON.parse(data.toString())
        // Forward PPMT messages to all connected clients
        io.emit('trading-state', msg)
      } catch (e) {
        console.error('[PPMT] Parse error:', e)
      }
    })

    ppmtWs.on('close', () => {
      console.log('[PPMT] Disconnected from Python V2 Server')
      ppmtConnected = false
      startDemoMode()
      io.emit('engine-status', { connected: false, mode: 'demo' })
    })

    ppmtWs.on('error', (err) => {
      console.log('[PPMT] Connection error, falling back to demo mode')
      ppmtConnected = false
      startDemoMode()
    })
  } catch (e) {
    console.log('[PPMT] Cannot connect, using demo mode')
    startDemoMode()
  }
}

function startDemoMode() {
  if (demoInterval || !useDemo) return
  useDemo = true
  demo.start()

  demoInterval = setInterval(() => {
    const state = demo.tick()
    io.emit('trading-state', state)
  }, 2000) // Update every 2 seconds

  console.log('[DEMO] Demo engine started (2s interval)')
}

// ─── Socket.io Events ──────────────────────────────────────
io.on('connection', (socket) => {
  console.log(`[Client] Connected: ${socket.id}`)

  // Send current mode on connect
  socket.emit('engine-status', {
    connected: ppmtConnected,
    mode: useDemo ? 'demo' : 'live',
  })

  // ─── Commands ───────────────────────────────────────────
  socket.on('start-trading', (data: { symbol?: string; timeframe?: string; capital?: number }) => {
    console.log('[CMD] Start trading:', data)
    if (useDemo) {
      if (data.symbol) demo.setSymbol(data.symbol)
      if (data.timeframe) demo.setTimeframe(data.timeframe)
      demo.start()
      if (!demoInterval) {
        demoInterval = setInterval(() => {
          const state = demo.tick()
          io.emit('trading-state', state)
        }, 2000)
      }
      socket.emit('command-result', { success: true, message: 'Demo trading started' })
    } else if (ppmtWs && ppmtConnected) {
      ppmtWs.send(JSON.stringify({ action: 'start', ...data }))
      socket.emit('command-result', { success: true, message: 'Live trading command sent' })
    } else {
      socket.emit('command-result', { success: false, message: 'No engine connected' })
    }
  })

  socket.on('stop-trading', () => {
    console.log('[CMD] Stop trading')
    if (useDemo) {
      demo.stop()
      if (demoInterval) {
        clearInterval(demoInterval)
        demoInterval = null
      }
      io.emit('trading-state', demo.tick())
      socket.emit('command-result', { success: true, message: 'Demo trading stopped' })
    } else if (ppmtWs && ppmtConnected) {
      ppmtWs.send(JSON.stringify({ action: 'stop' }))
      socket.emit('command-result', { success: true, message: 'Live stop command sent' })
    }
  })

  socket.on('kill-switch', () => {
    console.log('[CMD] KILL SWITCH ACTIVATED')
    if (useDemo) {
      demo.killSwitch()
      if (demoInterval) {
        clearInterval(demoInterval)
        demoInterval = null
      }
      io.emit('trading-state', demo.tick())
      io.emit('kill-switch-activated')
    } else if (ppmtWs && ppmtConnected) {
      ppmtWs.send(JSON.stringify({ action: 'kill_switch' }))
    }
    socket.emit('command-result', { success: true, message: 'Kill switch activated' })
  })

  socket.on('toggle-auto', (data: { enabled: boolean }) => {
    console.log('[CMD] Toggle auto mode:', data.enabled)
    if (!useDemo && ppmtWs && ppmtConnected) {
      ppmtWs.send(JSON.stringify({ action: 'toggle_auto', enabled: data.enabled }))
    }
    socket.emit('command-result', { success: true, message: `Auto mode ${data.enabled ? 'enabled' : 'disabled'}` })
  })

  socket.on('switch-symbol', (data: { symbol: string }) => {
    console.log('[CMD] Switch symbol:', data.symbol)
    if (useDemo) {
      demo.setSymbol(data.symbol)
    }
    socket.emit('command-result', { success: true, message: `Switched to ${data.symbol}` })
  })

  socket.on('switch-timeframe', (data: { timeframe: string }) => {
    console.log('[CMD] Switch timeframe:', data.timeframe)
    if (useDemo) {
      demo.setTimeframe(data.timeframe)
    }
    socket.emit('command-result', { success: true, message: `Switched to ${data.timeframe}` })
  })

  // ─── Request current state ──────────────────────────────
  socket.on('get-state', () => {
    if (useDemo) {
      socket.emit('trading-state', demo.tick())
    }
  })

  socket.on('disconnect', () => {
    console.log(`[Client] Disconnected: ${socket.id}`)
  })
})

// ─── Start Server ──────────────────────────────────────────
httpServer.listen(PORT, () => {
  console.log(`[Bridge] Trading Bridge running on port ${PORT}`)
  console.log(`[Bridge] PPMT backend: ${PPMT_WS_URL}`)

  // Try to connect to PPMT Python backend
  // If it fails, demo mode starts automatically
  connectPPMT()

  // Also start demo as default until PPMT connects
  startDemoMode()
})

process.on('SIGTERM', () => {
  console.log('[Bridge] Shutting down...')
  if (demoInterval) clearInterval(demoInterval)
  if (ppmtWs) ppmtWs.close()
  httpServer.close(() => process.exit(0))
})

process.on('SIGINT', () => {
  console.log('[Bridge] Shutting down...')
  if (demoInterval) clearInterval(demoInterval)
  if (ppmtWs) ppmtWs.close()
  httpServer.close(() => process.exit(0))
})
