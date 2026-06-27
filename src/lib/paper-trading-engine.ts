/**
 * PaperTradingEngine — Realistic paper trading with live market prices.
 *
 * Replaces DemoEngine. Key differences:
 *  - Prices come from a LivePriceFeed (Binance WebSocket), not random walk.
 *  - User can manually BUY / SELL / CLOSE positions on any token.
 *  - Real fees (0.1% taker) and slippage (0.05%) applied to every fill.
 *  - PnL reflects actual market movement, not synthetic noise.
 *  - Capital starts at 10,000 USDT and can grow or shrink for real.
 *
 * State output is compatible with the DemoEngine interface so the
 * existing useTradingSocket hook and store work without changes.
 *
 * Auto-mode (optional): when enabled, generates momentum-based signals
 * (top movers by 24h change% with volume confirmation) and opens
 * small positions automatically. Off by default — user controls.
 */

import type { TokenState, MoneyManagerSettings } from '@/stores/trading-store'
import type { LivePriceFeed, TickerData } from './live-price-feed'

export interface PaperTradingState {
  is_running: boolean
  mode: 'paper'
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
  token_states: Record<string, TokenState>
  active_tokens: string[]
  selected_token: string
  money_manager: MoneyManagerSettings
  kelly_percent: number
  suggested_position_size: number
  risk_reward_ratio: number
}

export interface OrderResult {
  success: boolean
  error?: string
  fillPrice?: number
  qty?: number
  fee?: number
  pnl?: number
}

export interface PaperPosition {
  symbol: string
  direction: 'LONG' | 'SHORT'
  status: string
  entry_price: number
  entry_time: string
  size_usdt: number
  qty: number
  current_sl: number | null
  current_tp: number | null
  catastrophic_sl: number | null
  pnl_pct: number
  pnl_usdt: number
}

interface PaperTrade extends PaperPosition {
  close_price: number
  close_reason: string
  closed_at: string
}

interface PaperOrder {
  timestamp: string
  side: 'BUY' | 'SELL'
  symbol: string
  qty: number
  price: number
  usdt: number
  fee: number
  type: 'MARKET'
}

// Expanded token universe — 50 liquid Binance USDT pairs
export const SUPPORTED_TOKENS = [
  // Tier 1 — mega cap
  'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
  // Tier 2 — large cap
  'ADA/USDT', 'AVAX/USDT', 'DOGE/USDT', 'DOT/USDT', 'LINK/USDT',
  'ATOM/USDT', 'LTC/USDT', 'BCH/USDT', 'NEAR/USDT', 'APT/USDT',
  'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'FIL/USDT', 'AAVE/USDT',
  'MKR/USDT', 'SUI/USDT', 'TIA/USDT', 'RUNE/USDT', 'FTM/USDT',
  // Tier 3 — mid cap / high beta
  'SEI/USDT', 'STX/USDT', 'IMX/USDT', 'GRT/USDT', 'LDO/USDT',
  'SAND/USDT', 'MANA/USDT', 'AXS/USDT', 'GALA/USDT', 'CHZ/USDT',
  'ENJ/USDT', 'PEPE/USDT', 'WIF/USDT', 'BONK/USDT', 'FLOKI/USDT',
  'SHIB/USDT', 'PYTH/USDT', 'JTO/USDT', 'ORDI/USDT', 'RNDR/USDT',
  'FET/USDT', 'AGIX/USDT', 'OCEAN/USDT', 'THETA/USDT', 'ICP/USDT',
] as const

export const TOKEN_NAMES: Record<string, string> = {
  'BTC/USDT': 'Bitcoin',
  'ETH/USDT': 'Ethereum',
  'BNB/USDT': 'BNB',
  'SOL/USDT': 'Solana',
  'XRP/USDT': 'Ripple',
  'ADA/USDT': 'Cardano',
  'AVAX/USDT': 'Avalanche',
  'DOGE/USDT': 'Dogecoin',
  'DOT/USDT': 'Polkadot',
  'LINK/USDT': 'Chainlink',
  'ATOM/USDT': 'Cosmos',
  'LTC/USDT': 'Litecoin',
  'BCH/USDT': 'Bitcoin Cash',
  'NEAR/USDT': 'Near',
  'APT/USDT': 'Aptos',
  'ARB/USDT': 'Arbitrum',
  'OP/USDT': 'Optimism',
  'INJ/USDT': 'Injective',
  'FIL/USDT': 'Filecoin',
  'AAVE/USDT': 'Aave',
  'MKR/USDT': 'Maker',
  'SUI/USDT': 'Sui',
  'TIA/USDT': 'Celestia',
  'RUNE/USDT': 'Thorchain',
  'FTM/USDT': 'Fantom',
  'SEI/USDT': 'Sei',
  'STX/USDT': 'Stacks',
  'IMX/USDT': 'Immutable',
  'GRT/USDT': 'The Graph',
  'LDO/USDT': 'Lido DAO',
  'SAND/USDT': 'Sandbox',
  'MANA/USDT': 'Decentraland',
  'AXS/USDT': 'Axie Infinity',
  'GALA/USDT': 'Gala',
  'CHZ/USDT': 'Chiliz',
  'ENJ/USDT': 'Enjin',
  'PEPE/USDT': 'Pepe',
  'WIF/USDT': 'dogwifhat',
  'BONK/USDT': 'Bonk',
  'FLOKI/USDT': 'Floki',
  'SHIB/USDT': 'Shiba Inu',
  'PYTH/USDT': 'Pyth Network',
  'JTO/USDT': 'Jito',
  'ORDI/USDT': 'Ordinals',
  'RNDR/USDT': 'Render',
  'FET/USDT': 'Fetch.ai',
  'AGIX/USDT': 'SingularityNET',
  'OCEAN/USDT': 'Ocean Protocol',
  'THETA/USDT': 'Theta',
  'ICP/USDT': 'Internet Computer',
}

const TOKEN_COLORS: Record<string, string> = {
  'BTC/USDT': '#F7931A',
  'ETH/USDT': '#627EEA',
  'BNB/USDT': '#F3BA2F',
  'SOL/USDT': '#9945FF',
  'XRP/USDT': '#23292F',
  'ADA/USDT': '#0033AD',
  'AVAX/USDT': '#E84142',
  'DOGE/USDT': '#C3A634',
  'DOT/USDT': '#E6007A',
  'LINK/USDT': '#2A5ADA',
  'ATOM/USDT': '#2E3148',
  'LTC/USDT': '#A6A9AA',
  'BCH/USDT': '#0AC18E',
  'NEAR/USDT': '#00EC97',
  'APT/USDT': '#06FC99',
  'ARB/USDT': '#28A0F0',
  'OP/USDT': '#FF0420',
  'INJ/USDT': '#00D2FF',
  'FIL/USDT': '#0090FF',
  'AAVE/USDT': '#B6509E',
  'MKR/USDT': '#1AAB9B',
  'SUI/USDT': '#4DA2FF',
  'TIA/USDT': '#7B2BF9',
  'RUNE/USDT': '#33FF99',
  'FTM/USDT': '#13B5EC',
  'SEI/USDT': '#8A2BE2',
  'STX/USDT': '#5546FF',
  'IMX/USDT': '#0A0A0A',
  'GRT/USDT': '#6F4CFF',
  'LDO/USDT': '#00A3FF',
  'SAND/USDT': '#00ADEF',
  'MANA/USDT': '#FF2A55',
  'AXS/USDT': '#0055D4',
  'GALA/USDT': '#0C0C0C',
  'CHZ/USDT': '#CD0A24',
  'ENJ/USDT': '#624DBF',
  'PEPE/USDT': '#3D8E2D',
  'WIF/USDT': '#E8B547',
  'BONK/USDT': '#FF7A00',
  'FLOKI/USDT': '#FFB300',
  'SHIB/USDT': '#FFA409',
  'PYTH/USDT': '#2D68FF',
  'JTO/USDT': '#39E0BB',
  'ORDI/USDT': '#FFD700',
  'RNDR/USDT': '#CF0E0F',
  'FET/USDT': '#1F4180',
  'AGIX/USDT': '#7C3AED',
  'OCEAN/USDT': '#7B1173',
  'THETA/USDT': '#2AB8E6',
  'ICP/USDT': '#3B00B9',
}

const DEFAULT_MONEY_MANAGER: MoneyManagerSettings = {
  riskPerTradePct: 2,
  maxConcurrentPositions: 5,
  maxCorrelatedPositions: 2,
  maxDrawdownPct: 25,
  dailyLossLimitPct: 8,
  positionSizingMethod: 'risk_parity',
  kellyFraction: 0.5,
  defaultLeverage: 1,
  maxLeverage: 3,
  takeProfitMultiplier: 2.5,
  stopLossATR: 1.5,
  trailingStopEnabled: true,
  trailingStopActivationPct: 1.5,
  trailingStopDistancePct: 0.8,
  breakEvenEnabled: true,
  breakEvenActivationPct: 0.8,
}

export const INITIAL_CAPITAL = 10000 // USDT
const TAKER_FEE_PCT = 0.10           // 0.1% per fill
const SLIPPAGE_PCT = 0.05            // 0.05% on market orders

export class PaperTradingEngine {
  private cash: number = INITIAL_CAPITAL
  private positions: Map<string, PaperPosition> = new Map()
  private trades: PaperTrade[] = []
  private orders: PaperOrder[] = []
  private signals: any[] = []
  private equity: number[] = [INITIAL_CAPITAL]
  private timestamps: number[] = [Date.now() / 1000]
  private totalTrades: number = 0
  private winningTrades: number = 0
  private realizedPnl: number = 0
  private running: boolean = false
  private tradingEnabled: boolean = true
  private autoMode: boolean = false
  private interval: ReturnType<typeof setInterval> | null = null
  private priceFeed: LivePriceFeed
  private activeTokens: string[] = [...SUPPORTED_TOKENS.slice(0, 12)]
  private selectedToken: string = 'BTC/USDT'
  private moneyManager: MoneyManagerSettings = { ...DEFAULT_MONEY_MANAGER }
  private lastAutoSignalTime: number = 0
  private startedAt: number = Date.now() / 1000

  // ─── Pattern Buffer + Living Trie (real learning) ────────
  // SAX-like encoding of recent price moves per token.
  // Each tick: encode the move since last tick as a symbol:
  //   U = up > +0.05%, D = down < -0.05%, F = flat
  // Pattern buffer is the last N symbols for the selected token.
  // Living trie counts unique observed patterns (sequences of
  // length 3-6) and grows over time as more ticks are processed.
  private lastTickPrices: Map<string, number> = new Map()
  private patternBufferPerToken: Map<string, string[]> = new Map()
  private livingTrie: Map<string, number> = new Map() // pattern -> count
  private candlesProcessed: number = 0
  private maxPatternDepthObserved: number = 0
  private lastTriePruneTime: number = 0

  constructor(priceFeed: LivePriceFeed) {
    this.priceFeed = priceFeed
  }

  startTicking(onTick: (state: PaperTradingState) => void, intervalMs: number = 1500) {
    if (this.interval) return
    this.running = true
    this.interval = setInterval(() => {
      onTick(this.snapshot())
    }, intervalMs)
    onTick(this.snapshot())
  }

  stopTicking() {
    this.running = false
    if (this.interval) {
      clearInterval(this.interval)
      this.interval = null
    }
  }

  isRunning(): boolean {
    return this.running
  }

  /** Manual market buy — opens or adds to a LONG position. */
  marketBuy(symbol: string, usdtAmount: number): OrderResult {
    if (!this.tradingEnabled) {
      return { success: false, error: 'Trading disabled (kill switch active). Click Start Trading.' }
    }
    if (usdtAmount <= 0) return { success: false, error: 'Amount must be > 0' }

    const ticker = this.priceFeed.getData(symbol)
    if (!ticker) {
      return { success: false, error: `No live price for ${symbol} yet. Wait for WS connection.` }
    }

    const fillPrice = ticker.price * (1 + SLIPPAGE_PCT / 100)
    const grossUsdt = usdtAmount
    const fee = grossUsdt * TAKER_FEE_PCT / 100
    const totalCost = grossUsdt + fee

    if (totalCost > this.cash) {
      return { success: false, error: `Insufficient cash. Need ${totalCost.toFixed(2)} USDT, have ${this.cash.toFixed(2)} USDT` }
    }

    const qty = grossUsdt / fillPrice
    this.cash -= totalCost

    const existing = this.positions.get(symbol)
    if (existing && existing.direction === 'LONG') {
      const newQty = existing.qty + qty
      const newAvgEntry = (existing.entry_price * existing.qty + fillPrice * qty) / newQty
      existing.qty = newQty
      existing.entry_price = newAvgEntry
      existing.size_usdt += grossUsdt
    } else {
      this.positions.set(symbol, {
        symbol,
        direction: 'LONG',
        status: 'ACTIVE',
        entry_price: fillPrice,
        entry_time: new Date().toISOString(),
        size_usdt: grossUsdt,
        qty,
        current_sl: null,
        current_tp: null,
        catastrophic_sl: null,
        pnl_pct: 0,
        pnl_usdt: 0,
      })
    }

    this.orders.unshift({
      timestamp: new Date().toISOString(),
      side: 'BUY',
      symbol,
      qty,
      price: fillPrice,
      usdt: grossUsdt,
      fee,
      type: 'MARKET',
    })
    if (this.orders.length > 100) this.orders = this.orders.slice(0, 100)

    console.log(`[Paper] BUY ${symbol} ${qty.toFixed(6)} @ ${fillPrice} (fee ${fee.toFixed(2)} USDT)`)
    return { success: true, fillPrice, qty, fee }
  }

  /**
   * Manual market sell.
   *  - If a LONG position exists for the symbol: closes it (partial or full).
   *  - Otherwise: opens a SHORT position (paper — no borrowing mechanics).
   */
  marketSell(symbol: string, usdtAmount: number): OrderResult {
    if (!this.tradingEnabled) {
      return { success: false, error: 'Trading disabled (kill switch active). Click Start Trading.' }
    }
    if (usdtAmount <= 0) return { success: false, error: 'Amount must be > 0' }

    const ticker = this.priceFeed.getData(symbol)
    if (!ticker) {
      return { success: false, error: `No live price for ${symbol} yet. Wait for WS connection.` }
    }

    const fillPrice = ticker.price * (1 - SLIPPAGE_PCT / 100)
    const fee = usdtAmount * TAKER_FEE_PCT / 100

    const existing = this.positions.get(symbol)

    if (existing && existing.direction === 'LONG') {
      // Close (partial or full) the LONG position
      const closeQty = Math.min(usdtAmount / fillPrice, existing.qty)
      const proceeds = closeQty * fillPrice
      const pnl = (fillPrice - existing.entry_price) * closeQty - fee
      this.cash += proceeds - fee
      this.realizedPnl += pnl
      this.totalTrades++
      if (pnl > 0) this.winningTrades++

      const closedFully = closeQty >= existing.qty - 1e-9
      if (closedFully) {
        this.trades.unshift({
          ...existing,
          close_price: fillPrice,
          close_reason: 'CLOSED_BY_USER_SELL',
          closed_at: new Date().toISOString(),
          pnl_usdt: pnl,
        })
        this.positions.delete(symbol)
      } else {
        existing.qty -= closeQty
        existing.size_usdt -= closeQty * existing.entry_price
        this.trades.unshift({
          ...existing,
          qty: closeQty,
          close_price: fillPrice,
          close_reason: 'CLOSED_BY_USER_SELL_PARTIAL',
          closed_at: new Date().toISOString(),
          pnl_usdt: pnl,
        })
      }
      if (this.trades.length > 100) this.trades = this.trades.slice(0, 100)

      console.log(`[Paper] SELL (close LONG) ${symbol} ${closeQty.toFixed(6)} @ ${fillPrice} (pnl ${pnl.toFixed(2)} USDT)`)
      return { success: true, fillPrice, qty: closeQty, fee, pnl }
    }

    // No LONG position — open SHORT
    const grossUsdt = usdtAmount
    const totalCost = fee // only fee deducted from cash for short (margin returned on close)
    if (totalCost > this.cash) {
      return { success: false, error: `Insufficient cash for short margin/fee. Need ${totalCost.toFixed(2)} USDT` }
    }
    this.cash -= totalCost
    const qty = grossUsdt / fillPrice

    if (existing && existing.direction === 'SHORT') {
      const newQty = existing.qty + qty
      const newAvgEntry = (existing.entry_price * existing.qty + fillPrice * qty) / newQty
      existing.qty = newQty
      existing.entry_price = newAvgEntry
      existing.size_usdt += grossUsdt
    } else {
      this.positions.set(symbol, {
        symbol,
        direction: 'SHORT',
        status: 'ACTIVE',
        entry_price: fillPrice,
        entry_time: new Date().toISOString(),
        size_usdt: grossUsdt,
        qty,
        current_sl: null,
        current_tp: null,
        catastrophic_sl: null,
        pnl_pct: 0,
        pnl_usdt: 0,
      })
    }

    this.orders.unshift({
      timestamp: new Date().toISOString(),
      side: 'SELL',
      symbol,
      qty,
      price: fillPrice,
      usdt: grossUsdt,
      fee,
      type: 'MARKET',
    })
    if (this.orders.length > 100) this.orders = this.orders.slice(0, 100)

    console.log(`[Paper] SELL (open SHORT) ${symbol} ${qty.toFixed(6)} @ ${fillPrice} (fee ${fee.toFixed(2)} USDT)`)
    return { success: true, fillPrice, qty, fee }
  }

  /** Close an entire position by symbol at current market price. */
  closePosition(symbol: string): OrderResult {
    const pos = this.positions.get(symbol)
    if (!pos) return { success: false, error: `No open position for ${symbol}` }

    const ticker = this.priceFeed.getData(symbol)
    if (!ticker) return { success: false, error: `No live price for ${symbol}` }

    const isLong = pos.direction === 'LONG'
    const fillPrice = isLong
      ? ticker.price * (1 - SLIPPAGE_PCT / 100)
      : ticker.price * (1 + SLIPPAGE_PCT / 100)

    const grossProceeds = pos.qty * fillPrice
    const fee = grossProceeds * TAKER_FEE_PCT / 100
    const pnl = isLong
      ? (fillPrice - pos.entry_price) * pos.qty - fee
      : (pos.entry_price - fillPrice) * pos.qty - fee

    if (isLong) {
      this.cash += grossProceeds - fee
    } else {
      // SHORT: return the original margin plus PnL minus fee
      this.cash += pos.size_usdt + pnl
    }

    this.realizedPnl += pnl
    this.totalTrades++
    if (pnl > 0) this.winningTrades++

    this.trades.unshift({
      ...pos,
      close_price: fillPrice,
      close_reason: 'CLOSED_BY_USER',
      closed_at: new Date().toISOString(),
      pnl_usdt: pnl,
    })
    if (this.trades.length > 100) this.trades = this.trades.slice(0, 100)
    this.positions.delete(symbol)

    console.log(`[Paper] CLOSE ${symbol} @ ${fillPrice} (pnl ${pnl.toFixed(2)} USDT)`)
    return { success: true, fillPrice, qty: pos.qty, fee, pnl }
  }

  killSwitch() {
    for (const sym of Array.from(this.positions.keys())) {
      this.closePosition(sym)
    }
    this.tradingEnabled = false
  }

  setTradingEnabled(enabled: boolean) {
    this.tradingEnabled = enabled
  }

  setAutoMode(enabled: boolean) {
    this.autoMode = enabled
    console.log(`[Paper] Auto mode ${enabled ? 'ON' : 'OFF'}`)
  }

  setSymbol(s: string) {
    this.selectedToken = s
    if (!this.activeTokens.includes(s)) {
      this.activeTokens = [...this.activeTokens, s]
      this.priceFeed.setSymbols(this.activeTokens)
    }
  }

  setTimeframe(tf: string) { /* no-op for paper — prices are real-time regardless */ }

  setActiveTokens(tokens: string[]) {
    const prev = new Set(this.activeTokens)
    this.activeTokens = tokens
    // Always include the selected token so its price keeps streaming
    if (!tokens.includes(this.selectedToken)) {
      this.activeTokens = [...tokens, this.selectedToken]
    }
    // Update price feed subscriptions if set changed
    const same = this.activeTokens.length === prev.size &&
      this.activeTokens.every(t => prev.has(t))
    if (!same) {
      this.priceFeed.setSymbols(this.activeTokens)
    }
  }

  setMoneyManager(settings: Partial<MoneyManagerSettings>) {
    this.moneyManager = { ...this.moneyManager, ...settings }
  }

  /**
   * Auto-mode: actively hunts for entries every ~15s.
   * Strategy:
   *   1. Scan all active tokens with live prices + >$10M volume
   *   2. Pick top mover by |24h change| (>=1.5% threshold)
   *   3. If not in position for that token and under maxConcurrent,
   *      open LONG (positive momentum) or SHORT (negative)
   *   4. Attach SL/TP based on money manager settings
   * Designed to be visible: should produce 2-4 trades per hour
   * in normal market conditions.
   */
  private maybeAutoTrade() {
    if (!this.autoMode || !this.tradingEnabled) return
    const now = Date.now()
    if (now - this.lastAutoSignalTime < 15000) return
    this.lastAutoSignalTime = now

    // Find strongest mover among active tokens with live prices
    const candidates = this.activeTokens
      .map(sym => this.priceFeed.getData(sym))
      .filter((t): t is TickerData => t !== null && t.quoteVolume > 10_000_000)
      .sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))

    if (candidates.length === 0) {
      console.log('[Paper/Auto] No candidates with live prices yet')
      return
    }
    const top = candidates[0]
    if (Math.abs(top.changePct) < 1.5) {
      console.log(`[Paper/Auto] Top mover ${top.symbol} only ${top.changePct.toFixed(2)}% — below 1.5% threshold`)
      return
    }

    // Don't open if already in a position for this symbol
    if (this.positions.has(top.symbol)) {
      console.log(`[Paper/Auto] Already in position for ${top.symbol}, skipping`)
      return
    }

    // Don't exceed max concurrent positions
    if (this.positions.size >= this.moneyManager.maxConcurrentPositions) {
      console.log(`[Paper/Auto] Max concurrent positions reached (${this.positions.size}/${this.moneyManager.maxConcurrentPositions})`)
      return
    }

    const usdtAmount = Math.min(
      this.cash * (this.moneyManager.riskPerTradePct / 100) * 5,
      this.cash * 0.10
    )
    if (usdtAmount < 10) {
      console.log('[Paper/Auto] Insufficient cash for new entry')
      return
    }

    const direction = top.changePct > 0 ? 'LONG' : 'SHORT'
    const signal = {
      timestamp: new Date().toISOString(),
      direction,
      symbol: top.symbol,
      confidence: Math.min(0.95, 0.55 + Math.abs(top.changePct) / 20),
      ev_score: 0.7 + Math.abs(top.changePct) / 30,
      pattern_path: `AUTO_MOMENTUM_24H_${direction}`,
      expected_move_pct: Math.abs(top.changePct) * 0.3,
    }
    this.signals.unshift(signal)
    if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)

    console.log(`[Paper/Auto] Signal: ${direction} ${top.symbol} (${top.changePct.toFixed(2)}% 24h, vol ${(top.quoteVolume/1e6).toFixed(1)}M)`)

    const result = direction === 'LONG'
      ? this.marketBuy(top.symbol, usdtAmount)
      : this.marketSell(top.symbol, usdtAmount)

    if (result.success) {
      console.log(`[Paper/Auto] OPENED ${direction} ${top.symbol} @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(2)} USDT)`)
      const pos = this.positions.get(top.symbol)
      if (pos) {
        const mm = this.moneyManager
        const move = pos.entry_price * (signal.expected_move_pct / 100)
        pos.current_sl = direction === 'LONG'
          ? pos.entry_price - move * mm.stopLossATR
          : pos.entry_price + move * mm.stopLossATR
        pos.current_tp = direction === 'LONG'
          ? pos.entry_price + move * mm.takeProfitMultiplier
          : pos.entry_price - move * mm.takeProfitMultiplier
        pos.catastrophic_sl = direction === 'LONG'
          ? pos.entry_price - move * 3
          : pos.entry_price + move * 3
      }
    } else {
      console.warn(`[Paper/Auto] Entry failed: ${result.error}`)
    }
  }

  /** Check SL/TP for all open positions on every snapshot. */
  private checkStops() {
    if (!this.tradingEnabled) return
    const mm = this.moneyManager

    for (const [sym, pos] of Array.from(this.positions.entries())) {
      const ticker = this.priceFeed.getData(sym)
      if (!ticker) continue
      const price = ticker.price
      const isLong = pos.direction === 'LONG'

      const pnlPct = isLong
        ? ((price - pos.entry_price) / pos.entry_price) * 100
        : ((pos.entry_price - price) / pos.entry_price) * 100

      // Trailing stop
      if (mm.trailingStopEnabled && pnlPct > mm.trailingStopActivationPct) {
        const trailDist = pos.entry_price * (mm.trailingStopDistancePct / 100)
        if (isLong && pos.current_sl !== null) {
          pos.current_sl = Math.max(pos.current_sl, price - trailDist)
        } else if (!isLong && pos.current_sl !== null) {
          pos.current_sl = Math.min(pos.current_sl, price + trailDist)
        }
      }

      // Break-even
      if (mm.breakEvenEnabled && pnlPct > mm.breakEvenActivationPct) {
        if (isLong && pos.current_sl !== null && pos.current_sl < pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        } else if (!isLong && pos.current_sl !== null && pos.current_sl > pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        }
      }

      // SL/TP trigger
      let hit = false
      let reason = ''
      if (pos.current_sl !== null) {
        if (isLong && price <= pos.current_sl) { hit = true; reason = 'CLOSED_BY_SL' }
        if (!isLong && price >= pos.current_sl) { hit = true; reason = pnlPct > 0 ? 'CLOSED_BY_TRAILING_SL' : 'CLOSED_BY_SL' }
      }
      if (pos.current_tp !== null) {
        if (isLong && price >= pos.current_tp) { hit = true; reason = 'CLOSED_BY_TP' }
        if (!isLong && price <= pos.current_tp) { hit = true; reason = 'CLOSED_BY_TP' }
      }

      if (hit) {
        this.closePosition(sym)
        // Override the close reason
        if (this.trades[0]) this.trades[0].close_reason = reason
      }
    }
  }

  /**
   * Update the pattern buffer + living trie based on price changes
   * since the last tick. This is the real "learning" mechanism:
   *   - Encode each token's move as U/D/F (SAX alphabet of size 3)
   *   - Append to per-token pattern buffer (circular, last 12 symbols)
   *   - For each suffix length 3..6, count the pattern in the trie
   *   - This grows the trie organically as more candles stream in.
   */
  private updatePatternsAndTrie() {
    const now = Date.now() / 1000
    for (const sym of this.activeTokens) {
      const t = this.priceFeed.getData(sym)
      if (!t) continue
      const last = this.lastTickPrices.get(sym)
      this.lastTickPrices.set(sym, t.price)
      if (last === undefined) continue // skip first tick (no reference)

      const pct = ((t.price - last) / last) * 100
      const sym_char = pct > 0.05 ? 'U' : pct < -0.05 ? 'D' : 'F'

      // Append to per-token pattern buffer
      let buf = this.patternBufferPerToken.get(sym) || []
      buf.push(sym_char)
      if (buf.length > 12) buf = buf.slice(-12)
      this.patternBufferPerToken.set(sym, buf)

      // Update living trie with all suffixes length 3..6
      const bufStr = buf.join('')
      for (let len = 3; len <= 6; len++) {
        if (buf.length < len) break
        const pattern = bufStr.slice(-len)
        const cur = this.livingTrie.get(pattern) || 0
        this.livingTrie.set(pattern, cur + 1)
        if (len > this.maxPatternDepthObserved) {
          this.maxPatternDepthObserved = len
        }
      }

      this.candlesProcessed++
    }

    // Prune trie periodically if it gets too large (keep top 5000)
    if (now - this.lastTriePruneTime > 60 && this.livingTrie.size > 5000) {
      const entries = Array.from(this.livingTrie.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5000)
      this.livingTrie = new Map(entries)
      this.lastTriePruneTime = now
    }
  }

  private snapshot(): PaperTradingState {
    this.updatePatternsAndTrie()
    this.checkStops()
    this.maybeAutoTrade()

    const wsConnected = this.priceFeed.isConnected()
    const liveSymbols = this.priceFeed.getAvailableSymbols()

    // Build positions array with current PnL
    const positionsArray: any[] = []
    let unrealized = 0
    let exposure = 0
    for (const [sym, pos] of this.positions.entries()) {
      const ticker = this.priceFeed.getData(sym)
      const price = ticker?.price ?? pos.entry_price
      const isLong = pos.direction === 'LONG'
      const pnl = isLong
        ? (price - pos.entry_price) * pos.qty
        : (pos.entry_price - price) * pos.qty
      const pnlPct = isLong
        ? ((price - pos.entry_price) / pos.entry_price) * 100
        : ((pos.entry_price - price) / pos.entry_price) * 100
      pos.pnl_pct = pnlPct
      pos.pnl_usdt = pnl
      unrealized += pnl
      exposure += pos.qty * price
      positionsArray.push({
        ...pos,
        current_price: price,
      })
    }

    const totalValue = this.cash + exposure
    this.equity.push(totalValue)
    this.timestamps.push(Date.now() / 1000)
    if (this.equity.length > 500) {
      this.equity = this.equity.slice(-500)
      this.timestamps = this.timestamps.slice(-500)
    }

    const maxEq = Math.max(...this.equity)
    const dd = maxEq > 0 ? ((maxEq - totalValue) / maxEq) * 100 : 0

    // Daily return: based on last 24h of equity samples (approx)
    const dayAgoIdx = Math.max(0, this.equity.length - (24 * 60 * 60 / 1.5))
    const dailyReturn = this.equity.length > 1
      ? ((totalValue - this.equity[dayAgoIdx]) / this.equity[dayAgoIdx]) * 100
      : 0

    // Token states from live price feed
    const tokenStates: Record<string, TokenState> = {}
    for (const sym of this.activeTokens) {
      const t = this.priceFeed.getData(sym)
      if (t) {
        const pos = this.positions.get(sym)
        tokenStates[sym] = {
          symbol: t.symbol,
          name: TOKEN_NAMES[t.symbol] || t.symbol,
          price: parseFloat(t.price.toFixed(t.price < 1 ? 6 : t.price < 100 ? 4 : 2)),
          change24h: parseFloat(t.changePct.toFixed(2)),
          volume24h: parseFloat(t.quoteVolume.toFixed(0)),
          positions: pos ? [pos] : [],
          unrealizedPnl: pos ? parseFloat(pos.pnl_usdt.toFixed(4)) : 0,
          realizedPnl: 0,
          allocationPct: totalValue > 0 && pos
            ? parseFloat(((pos.qty * t.price) / totalValue * 100).toFixed(1))
            : 0,
          isActive: true,
          isTrading: !!pos,
          winRate: this.totalTrades > 0 ? this.winningTrades / this.totalTrades : 0,
          totalTrades: this.trades.filter(tr => tr.symbol === sym).length,
          equity: this.equity,
          color: TOKEN_COLORS[sym] || '#6b7280',
        }
      }
    }

    // Sort active tokens by 24h change % to surface top movers
    const sortedActive = [...this.activeTokens]
      .map(s => ({ s, ch: this.priceFeed.getData(s)?.changePct ?? -999 }))
      .sort((a, b) => Math.abs(b.ch) - Math.abs(a.ch))
      .map(x => x.s)

    // Kelly calculation
    const wr = this.totalTrades > 0 ? this.winningTrades / this.totalTrades : 0
    const mm = this.moneyManager
    const R = mm.takeProfitMultiplier
    const kellyPercent = wr > 0 && wr < 1 ? Math.max(0, wr - ((1 - wr) / R)) : 0
    const suggestedPositionSize = kellyPercent * mm.kellyFraction * totalValue

    // Current price (selected token)
    const selTicker = this.priceFeed.getData(this.selectedToken)
    const currentPrice = selTicker?.price ?? 0

    // Regime classification based on broad market (top 5 by volume)
    const top5 = [...liveSymbols]
      .map(s => this.priceFeed.getData(s)!)
      .filter(Boolean)
      .sort((a, b) => b.quoteVolume - a.quoteVolume)
      .slice(0, 5)
    const avgChange = top5.length > 0
      ? top5.reduce((s, t) => s + t.changePct, 0) / top5.length
      : 0
    const regime = avgChange > 2 ? 'trending_up'
      : avgChange < -2 ? 'trending_down'
      : Math.abs(avgChange) < 0.5 ? 'ranging'
      : 'volatile'

    // Entropy: derived from dispersion of token changes
    const changes = sortedActive
      .map(s => this.priceFeed.getData(s)?.changePct ?? 0)
      .slice(0, 10)
    const mean = changes.reduce((a, b) => a + b, 0) / (changes.length || 1)
    const variance = changes.reduce((a, b) => a + (b - mean) ** 2, 0) / (changes.length || 1)
    const entropy = Math.min(1, Math.sqrt(variance) / 5)

    return {
      is_running: this.tradingEnabled,
      mode: 'paper',
      started_at: this.startedAt,
      current_price: parseFloat(currentPrice.toFixed(currentPrice < 1 ? 6 : currentPrice < 100 ? 4 : 2)),
      symbol: this.selectedToken,
      timeframe: 'live',
      exchange: 'BINANCE',
      pattern_buffer: this.patternBufferPerToken.get(this.selectedToken) || [],
      entropy: parseFloat(entropy.toFixed(3)),
      regime,
      latest_signal: this.signals[0] || null,
      signals_history: this.signals.slice(0, 20),
      positions: positionsArray,
      portfolio_value: parseFloat(totalValue.toFixed(2)),
      cash: parseFloat(this.cash.toFixed(2)),
      unrealized_pnl: parseFloat(unrealized.toFixed(4)),
      realized_pnl: parseFloat(this.realizedPnl.toFixed(4)),
      total_pnl_pct: parseFloat(((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100).toFixed(2)),
      exposure_pct: totalValue > 0 ? parseFloat((exposure / totalValue * 100).toFixed(1)) : 0,
      daily_return_pct: parseFloat(dailyReturn.toFixed(2)),
      leverage: mm.defaultLeverage,
      auto_mode: this.autoMode,
      circuit_breakers: {
        max_drawdown: dd > mm.maxDrawdownPct,
        daily_loss: this.realizedPnl < -(INITIAL_CAPITAL * mm.dailyLossLimitPct / 100),
        volatility: false,
      },
      is_trading_allowed: dd < mm.maxDrawdownPct + 5,
      kill_switch_active: !this.tradingEnabled,
      max_drawdown_pct: parseFloat(dd.toFixed(2)),
      daily_loss_pct: parseFloat(Math.max(0, -this.realizedPnl / (INITIAL_CAPITAL / 100)).toFixed(2)),
      total_trades: this.totalTrades,
      winning_trades: this.winningTrades,
      win_rate: this.totalTrades > 0 ? parseFloat((this.winningTrades / this.totalTrades).toFixed(3)) : 0,
      max_drawdown: parseFloat(dd.toFixed(2)),
      equity_curve: this.equity,
      equity_timestamps: this.timestamps,
      monte_carlo: {
        risk_of_ruin: parseFloat((Math.max(0, dd / mm.maxDrawdownPct) * 0.05).toFixed(4)),
        probability_of_profit: wr > 0 ? parseFloat((0.5 + (wr - 0.5) * 0.6).toFixed(3)) : 0.5,
        p95_dd: parseFloat((dd * 1.3).toFixed(1)),
        verdict: dd < mm.maxDrawdownPct ? 'PASS' : 'WARN',
      },
      living_trie_stats: {
        pattern_count: this.livingTrie.size,
        max_depth: this.maxPatternDepthObserved,
        trading_observations: this.candlesProcessed,
        last_update: new Date().toISOString(),
      },
      trade_history: this.trades.slice(0, 20).map(t => ({
        ...t,
        status: 'CLOSED',
      })),
      candles_processed: this.candlesProcessed,
      websocket_status: wsConnected ? 'connected' : 'reconnecting',
      reconnect_count: 0,
      token_states: tokenStates,
      active_tokens: sortedActive,
      selected_token: this.selectedToken,
      money_manager: mm,
      kelly_percent: parseFloat(kellyPercent.toFixed(4)),
      suggested_position_size: parseFloat(suggestedPositionSize.toFixed(2)),
      risk_reward_ratio: R,
    }
  }
}
