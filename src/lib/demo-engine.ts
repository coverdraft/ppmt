/**
 * DemoEngine — Client-side demo data generator for PPMT Trading Terminal.
 *
 * Generates realistic-looking trading data for demo mode when the
 * Socket.io Trading Bridge is not available. Now supports multi-token
 * trading with portfolio management and money management simulation.
 */

import type { TokenState, MoneyManagerSettings } from '@/stores/trading-store'

export interface DemoTradingState {
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
  // Multi-token
  token_states: Record<string, TokenState>
  active_tokens: string[]
  selected_token: string
  money_manager: MoneyManagerSettings
  kelly_percent: number
  suggested_position_size: number
  risk_reward_ratio: number
}

// Token base prices for simulation
const TOKEN_BASE_PRICES: Record<string, number> = {
  'SOL/USDT': 172.50,
  'BTC/USDT': 67450.0,
  'ETH/USDT': 3650.0,
  'DOGE/USDT': 0.164,
  'AVAX/USDT': 38.50,
  'ADA/USDT': 0.45,
  'LINK/USDT': 14.80,
  'DOT/USDT': 7.20,
  'MATIC/USDT': 0.72,
  'UNI/USDT': 7.85,
}

const TOKEN_COLORS: Record<string, string> = {
  'SOL/USDT': '#9945FF',
  'BTC/USDT': '#F7931A',
  'ETH/USDT': '#627EEA',
  'DOGE/USDT': '#C3A634',
  'AVAX/USDT': '#E84142',
  'ADA/USDT': '#0033AD',
  'LINK/USDT': '#2A5ADA',
  'DOT/USDT': '#E6007A',
  'MATIC/USDT': '#8247E5',
  'UNI/USDT': '#FF007A',
}

const TOKEN_NAMES: Record<string, string> = {
  'SOL/USDT': 'Solana',
  'BTC/USDT': 'Bitcoin',
  'ETH/USDT': 'Ethereum',
  'DOGE/USDT': 'Dogecoin',
  'AVAX/USDT': 'Avalanche',
  'ADA/USDT': 'Cardano',
  'LINK/USDT': 'Chainlink',
  'DOT/USDT': 'Polkadot',
  'MATIC/USDT': 'Polygon',
  'UNI/USDT': 'Uniswap',
}

interface TokenSim {
  symbol: string
  price: number
  change24h: number
  volume24h: number
  positions: any[]
  unrealizedPnl: number
  realizedPnl: number
  allocationPct: number
  isActive: boolean
  isTrading: boolean
  winRate: number
  totalTrades: number
  winningTrades: number
  equity: number[]
  activePosition: any | null
  signals: any[]
  trades: any[]
}

const DEFAULT_MONEY_MANAGER: MoneyManagerSettings = {
  riskPerTradePct: 2,
  maxConcurrentPositions: 3,
  maxCorrelatedPositions: 1,
  maxDrawdownPct: 15,
  dailyLossLimitPct: 5,
  positionSizingMethod: 'risk_parity',
  kellyFraction: 0.5,
  defaultLeverage: 3,
  maxLeverage: 10,
  takeProfitMultiplier: 2.5,
  stopLossATR: 1.5,
  trailingStopEnabled: true,
  trailingStopActivationPct: 1.0,
  trailingStopDistancePct: 0.5,
  breakEvenEnabled: true,
  breakEvenActivationPct: 0.5,
}

export class DemoEngine {
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
  // tradingEnabled controls whether NEW positions can be opened.
  // The ticker (this.running) keeps flowing regardless, so prices,
  // signals, equity curve, and open-position management continue
  // even after Stop Trading or Kill Switch. This guarantees the
  // terminal is ALWAYS in real-time, per user requirement.
  private tradingEnabled: boolean = true
  private patterns: string[] = ['a', 'b', 'c', 'd', 'e']
  private patternIdx: number = 0
  private activePosition: any = null
  private interval: ReturnType<typeof setInterval> | null = null

  // Multi-token
  private tokenSims: Record<string, TokenSim> = {}
  private activeTokens: string[] = ['SOL/USDT', 'BTC/USDT', 'ETH/USDT']
  private selectedToken: string = 'SOL/USDT'
  private moneyManager: MoneyManagerSettings = { ...DEFAULT_MONEY_MANAGER }

  constructor() {
    this.initTokenSims()
  }

  private initTokenSims() {
    for (const [sym, basePrice] of Object.entries(TOKEN_BASE_PRICES)) {
      this.tokenSims[sym] = {
        symbol: sym,
        price: basePrice * (0.98 + Math.random() * 0.04), // slight variation
        change24h: (Math.random() - 0.4) * 8,
        volume24h: 1000000 + Math.random() * 50000000,
        positions: [],
        unrealizedPnl: 0,
        realizedPnl: (Math.random() - 0.3) * 20, // slight positive bias
        allocationPct: sym === 'SOL/USDT' ? 40 : sym === 'BTC/USDT' ? 35 : 25,
        isActive: this.activeTokens.includes(sym),
        isTrading: this.activeTokens.includes(sym),
        winRate: 0.5 + Math.random() * 0.15,
        totalTrades: Math.floor(Math.random() * 30),
        winningTrades: 0,
        equity: [1000],
        activePosition: null,
        signals: [],
        trades: [],
      }
      const sim = this.tokenSims[sym]
      sim.winningTrades = Math.floor(sim.totalTrades * sim.winRate)
    }
  }

  start() {
    if (this.running) return
    this.running = true
  }

  stop() {
    this.running = false
    if (this.interval) {
      clearInterval(this.interval)
      this.interval = null
    }
  }

  isRunning() {
    return this.running
  }

  startTicking(onTick: (state: DemoTradingState) => void, intervalMs: number = 2000) {
    if (this.interval) return
    this.running = true
    this.interval = setInterval(() => {
      const state = this.tick()
      onTick(state)
    }, intervalMs)
    // Emit first tick immediately
    onTick(this.tick())
  }

  stopTicking() {
    this.stop()
  }

  private tickTokenSim(sim: TokenSim): TokenSim {
    // Price movement per token
    const volatility = sim.symbol === 'BTC/USDT' ? 0.0015 : sim.symbol === 'ETH/USDT' ? 0.002 : 0.003
    const drift = (Math.random() - 0.48) * volatility
    sim.price *= (1 + drift)
    sim.change24h += (Math.random() - 0.5) * 0.2
    sim.volume24h *= (1 + (Math.random() - 0.5) * 0.01)

    // Generate signal for this token
    if (sim.isActive && Math.random() < 0.06) {
      const direction = Math.random() > 0.5 ? 'LONG' : 'SHORT'
      const signal = {
        timestamp: new Date().toISOString(),
        direction,
        symbol: sim.symbol,
        confidence: 0.55 + Math.random() * 0.35,
        ev_score: 0.7 + Math.random() * 0.5,
        pattern_path: this.patterns.slice(0, this.patternIdx + 1).join('-'),
        expected_move_pct: 0.15 + Math.random() * 0.5,
      }
      sim.signals.unshift(signal)
      if (sim.signals.length > 50) sim.signals = sim.signals.slice(0, 50)

      // Open position based on money manager (only if trading is enabled)
      if (this.tradingEnabled && !sim.activePosition && signal.confidence > 0.6) {
        const entry = sim.price
        const move = entry * (signal.expected_move_pct / 100)
        // Use money manager settings for position sizing
        const mm = this.moneyManager
        const riskAmount = 1000 * (mm.riskPerTradePct / 100)
        const sizeUsdt = Math.min(riskAmount * mm.takeProfitMultiplier, 1000 * (sim.allocationPct / 100) * 0.1)

        sim.activePosition = {
          symbol: sim.symbol,
          direction,
          status: 'ACTIVE',
          entry_price: entry,
          entry_time: new Date().toISOString(),
          size_usdt: sizeUsdt,
          current_sl: direction === 'LONG' ? entry - move * (mm.stopLossATR || 1.2) : entry + move * (mm.stopLossATR || 1.2),
          current_tp: direction === 'LONG' ? entry + move * mm.takeProfitMultiplier : entry - move * mm.takeProfitMultiplier,
          catastrophic_sl: direction === 'LONG' ? entry - move * 3.0 : entry + move * 3.0,
          pnl_pct: 0,
          pnl_usdt: 0,
        }
        sim.positions = sim.activePosition ? [sim.activePosition] : []
      }
    }

    // Check position
    if (sim.activePosition) {
      const pos = sim.activePosition
      const isLong = pos.direction === 'LONG'
      const pnlPct = isLong
        ? ((sim.price - pos.entry_price) / pos.entry_price) * 100
        : ((pos.entry_price - sim.price) / pos.entry_price) * 100

      pos.pnl_pct = pnlPct
      pos.pnl_usdt = pos.size_usdt * (pnlPct / 100)

      // Check trailing stop
      const mm = this.moneyManager
      if (mm.trailingStopEnabled && pnlPct > mm.trailingStopActivationPct) {
        // Trail the stop loss
        const trailDist = pos.entry_price * (mm.trailingStopDistancePct / 100)
        if (isLong) {
          const newSl = Math.max(pos.current_sl, sim.price - trailDist)
          pos.current_sl = newSl
        } else {
          const newSl = Math.min(pos.current_sl, sim.price + trailDist)
          pos.current_sl = newSl
        }
      }

      // Check break-even
      if (mm.breakEvenEnabled && pnlPct > mm.breakEvenActivationPct) {
        if (isLong && pos.current_sl < pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        } else if (!isLong && pos.current_sl > pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        }
      }

      // Check SL/TP
      let closed = false
      let closeReason = ''
      if (isLong) {
        if (sim.price <= pos.current_sl) { closed = true; closeReason = pnlPct > 0 ? 'CLOSED_BY_TRAILING_SL' : 'CLOSED_BY_SL' }
        if (sim.price >= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      } else {
        if (sim.price >= pos.current_sl) { closed = true; closeReason = pnlPct > 0 ? 'CLOSED_BY_TRAILING_SL' : 'CLOSED_BY_SL' }
        if (sim.price <= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      }

      if (!closed && Math.random() < 0.015) {
        closed = true
        closeReason = Math.abs(pnlPct) < 0.05 ? 'CLOSED_DIVERGENCE' : 'CLOSED_BY_SL'
      }

      if (closed) {
        sim.totalTrades++
        const won = pnlPct > 0
        if (won) sim.winningTrades++
        sim.realizedPnl += pos.pnl_usdt
        sim.winRate = sim.totalTrades > 0 ? sim.winningTrades / sim.totalTrades : 0

        sim.trades.unshift({
          ...pos,
          close_price: sim.price,
          close_reason: closeReason,
          closed_at: new Date().toISOString(),
        })
        if (sim.trades.length > 100) sim.trades = sim.trades.slice(0, 100)
        sim.activePosition = null
        sim.positions = []
      }

      sim.unrealizedPnl = pos.pnl_usdt
    } else {
      sim.unrealizedPnl = 0
      sim.positions = []
    }

    return sim
  }

  tick(): DemoTradingState {
    // Simulate price movement (random walk with slight drift) for primary symbol
    const volatility = 0.003
    const drift = (Math.random() - 0.48) * volatility
    this.price *= (1 + drift)
    this.price = Math.max(this.price * 0.95, Math.min(this.price * 1.05, this.price))

    // Advance pattern
    this.patternIdx = (this.patternIdx + 1) % this.patterns.length

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

      // Maybe open position from signal (only if trading is enabled)
      if (this.tradingEnabled && !this.activePosition && signal.confidence > 0.6) {
        const entry = this.price
        const move = entry * (signal.expected_move_pct / 100)
        const mm = this.moneyManager
        this.activePosition = {
          symbol: this.symbol,
          direction,
          status: 'ACTIVE',
          entry_price: entry,
          entry_time: new Date().toISOString(),
          size_usdt: 100,
          current_sl: direction === 'LONG' ? entry - move * (mm.stopLossATR || 1.2) : entry + move * (mm.stopLossATR || 1.2),
          current_tp: direction === 'LONG' ? entry + move * mm.takeProfitMultiplier : entry - move * mm.takeProfitMultiplier,
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

      let closed = false
      let closeReason = ''
      if (isLong) {
        if (this.price <= pos.current_sl) { closed = true; closeReason = 'CLOSED_BY_SL' }
        if (this.price >= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      } else {
        if (this.price >= pos.current_sl) { closed = true; closeReason = 'CLOSED_BY_SL' }
        if (this.price <= pos.current_tp) { closed = true; closeReason = 'CLOSED_BY_TP' }
      }

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

    // ─── Multi-token tick ────────────────────────
    const tokenStates: Record<string, TokenState> = {}
    for (const sym of this.activeTokens) {
      const sim = this.tokenSims[sym]
      if (sim) {
        this.tickTokenSim(sim)
        tokenStates[sym] = {
          symbol: sim.symbol,
          name: TOKEN_NAMES[sim.symbol] || sim.symbol,
          price: parseFloat(sim.price.toFixed(sim.price < 1 ? 6 : sim.price < 100 ? 4 : 2)),
          change24h: parseFloat(sim.change24h.toFixed(2)),
          volume24h: parseFloat(sim.volume24h.toFixed(0)),
          positions: sim.positions,
          unrealizedPnl: parseFloat(sim.unrealizedPnl.toFixed(4)),
          realizedPnl: parseFloat(sim.realizedPnl.toFixed(4)),
          allocationPct: sim.allocationPct,
          isActive: sim.isActive,
          isTrading: sim.isTrading,
          winRate: parseFloat(sim.winRate.toFixed(3)),
          totalTrades: sim.totalTrades,
          equity: sim.equity,
          color: TOKEN_COLORS[sim.symbol] || '#6b7280',
        }
      }
    }

    // Kelly calculation
    const wr = this.totalTrades > 0 ? this.winningTrades / this.totalTrades : 0
    const mm = this.moneyManager
    const R = mm.takeProfitMultiplier
    const kellyPercent = wr > 0 && wr < 1 ? Math.max(0, wr - ((1 - wr) / R)) : 0
    const suggestedPositionSize = kellyPercent * mm.kellyFraction * totalValue

    return {
      // is_running reflects whether NEW positions can be opened.
      // The ticker itself (this.running) keeps the terminal alive
      // even when trading is disabled, so the user always sees
      // real-time data flowing.
      is_running: this.tradingEnabled,
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
      leverage: mm.defaultLeverage,
      auto_mode: true,
      circuit_breakers: {
        max_drawdown: dd > mm.maxDrawdownPct,
        daily_loss: this.realizedPnl < -(1000 * mm.dailyLossLimitPct / 100),
        volatility: false,
      },
      is_trading_allowed: dd < mm.maxDrawdownPct + 5,
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
      // Multi-token
      token_states: tokenStates,
      active_tokens: this.activeTokens,
      selected_token: this.selectedToken,
      money_manager: mm,
      kelly_percent: parseFloat(kellyPercent.toFixed(4)),
      suggested_position_size: parseFloat(suggestedPositionSize.toFixed(2)),
      risk_reward_ratio: R,
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
    // Kill all token positions too
    for (const sim of Object.values(this.tokenSims)) {
      if (sim.activePosition) {
        sim.realizedPnl += sim.activePosition.pnl_usdt
        sim.trades.unshift({
          ...sim.activePosition,
          close_price: sim.price,
          close_reason: 'CLOSED_KILL_SWITCH',
          closed_at: new Date().toISOString(),
        })
        sim.totalTrades++
        if (sim.activePosition.pnl_pct > 0) sim.winningTrades++
        sim.activePosition = null
        sim.positions = []
      }
    }
    // IMPORTANT: do NOT stop the ticker. Only disable new entries.
    // The user can resume by clicking Start Trading. Prices, signals,
    // and chart updates keep flowing — terminal stays in real-time.
    this.tradingEnabled = false
  }

  setSymbol(s: string) { this.symbol = s; this.selectedToken = s }
  setTimeframe(tf: string) { this.timeframe = tf }
  setActiveTokens(tokens: string[]) { this.activeTokens = tokens }
  /**
   * Enable/disable opening of NEW positions.
   * The ticker keeps running regardless — only new entries are gated.
   * Use this for Start/Stop Trading without freezing the terminal.
   */
  setTradingEnabled(enabled: boolean) { this.tradingEnabled = enabled }
  setMoneyManager(settings: Partial<MoneyManagerSettings>) {
    this.moneyManager = { ...this.moneyManager, ...settings }
  }
}
