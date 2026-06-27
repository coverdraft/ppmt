/**
 * Trading Store — Zustand store for real-time trading state.
 *
 * Manages all state received from the PPMT Trading Bridge
 * via Socket.io, plus local UI state including chart data series.
 * Now supports multi-token trading, portfolio management, and money management.
 */
import { create } from 'zustand'

export interface Position {
  symbol: string
  direction: 'LONG' | 'SHORT'
  status: string
  entry_price: number
  entry_time: string
  size_usdt: number
  current_sl: number | null   // null for manual entries (no auto SL set)
  current_tp: number | null   // null for manual entries
  catastrophic_sl: number | null
  pnl_pct: number
  pnl_usdt: number
  expected_sequence?: string[][]
  sequence_index?: number
}

export interface Signal {
  timestamp: string
  direction: 'LONG' | 'SHORT'
  symbol: string
  confidence: number
  ev_score: number
  pattern_path: string
  expected_move_pct: number
}

export interface TradeRecord {
  symbol: string
  direction: string
  status: string
  entry_price: number
  entry_time: string
  close_price?: number
  close_reason?: string
  closed_at?: string
  pnl_pct: number
  pnl_usdt: number
  size_usdt: number
}

export interface MonteCarloResult {
  risk_of_ruin: number
  probability_of_profit: number
  p95_dd: number
  verdict: string
}

export interface CircuitBreakers {
  max_drawdown: boolean
  daily_loss: boolean
  volatility: boolean
}

// ─── Multi-Token Types ────────────────────────────────────

export interface TokenState {
  symbol: string
  name: string
  price: number
  change24h: number
  volume24h: number
  positions: Position[]
  unrealizedPnl: number
  realizedPnl: number
  allocationPct: number
  isActive: boolean
  isTrading: boolean
  winRate: number
  totalTrades: number
  equity: number[]
  color: string
}

export interface MoneyManagerSettings {
  riskPerTradePct: number        // % of portfolio risked per trade (0.5-5%)
  maxConcurrentPositions: number // max open positions at once
  maxCorrelatedPositions: number // max positions in correlated tokens
  maxDrawdownPct: number        // max allowed drawdown before circuit breaker
  dailyLossLimitPct: number     // daily loss limit as % of portfolio
  positionSizingMethod: 'fixed' | 'kelly' | 'risk_parity' | 'volatility_adj'
  kellyFraction: number         // fraction of Kelly to use (0.25-1.0)
  defaultLeverage: number       // default leverage for new positions
  maxLeverage: number           // maximum allowed leverage
  takeProfitMultiplier: number  // TP = x * risk
  stopLossATR: number           // SL distance in ATR multiples
  trailingStopEnabled: boolean
  trailingStopActivationPct: number  // activate trailing after x% profit
  trailingStopDistancePct: number    // trailing distance in %
  breakEvenEnabled: boolean
  breakEvenActivationPct: number     // move SL to entry after x% profit
}

export interface PortfolioAllocation {
  symbol: string
  value: number
  pct: number
  pnl: number
  color: string
}

// ─── Chart Data Points ────────────────────────────────────

export interface EntropyPoint {
  time: number
  value: number
}

export interface RegimePoint {
  time: number
  regime: string
  regimeNum: number // 0=volatile, 1=ranging, 2=trending_down, 3=trending_up
}

export interface PricePoint {
  time: number
  price: number
  isTrade?: boolean
  tradeDirection?: 'LONG' | 'SHORT'
  tradeAction?: 'OPEN' | 'CLOSE'
  pnl?: number
}

export interface WinRatePoint {
  time: number
  winRate: number
  trades: number
  wins: number
}

export interface ConfidencePoint {
  time: number
  confidence: number
  evScore: number
  direction: string
}

export interface PatternMatchPoint {
  time: number
  matchScore: number
  pathLength: number
}

export interface LearningStagePoint {
  time: number
  stage: string
  stageNum: number
  wr: number
  pf: number
}

export interface TradingState {
  // Connection
  isConnected: boolean
  engineMode: 'demo' | 'paper' | 'live' | 'disconnected'
  isRunning: boolean

  // Market
  currentPrice: number
  symbol: string
  timeframe: string
  exchange: string

  // Pattern / SAX
  patternBuffer: string[]
  entropy: number
  regime: string

  // Signals
  latestSignal: Signal | null
  signalsHistory: Signal[]

  // Positions
  positions: Position[]

  // Portfolio
  portfolioValue: number
  cash: number
  unrealizedPnl: number
  realizedPnl: number
  totalPnlPct: number
  exposurePct: number
  dailyReturnPct: number
  leverage: number
  autoMode: boolean

  // Risk
  circuitBreakers: CircuitBreakers
  isTradingAllowed: boolean
  killSwitchActive: boolean
  maxDrawdownPct: number
  dailyLossPct: number

  // Performance
  totalTrades: number
  winningTrades: number
  winRate: number
  maxDrawdown: number

  // Curves
  equityCurve: number[]
  equityTimestamps: number[]

  // Monte Carlo
  monteCarlo: MonteCarloResult | null

  // Living Trie
  livingTrieStats: {
    pattern_count: number
    max_depth: number
    trading_observations: number
    last_update: string
  } | null

  // History
  tradeHistory: TradeRecord[]
  candlesProcessed: number
  websocketStatus: string

  // ─── Chart Data Series ─────────────────────
  entropyHistory: EntropyPoint[]
  regimeHistory: RegimePoint[]
  priceHistory: PricePoint[]
  winRateHistory: WinRatePoint[]
  confidenceHistory: ConfidencePoint[]
  patternMatchHistory: PatternMatchPoint[]
  learningStageHistory: LearningStagePoint[]
  learningStage: string
  driftDetected: boolean
  lastRetrainTime: number | null

  // ─── Multi-Token ───────────────────────────
  activeTokens: string[]
  tokenStates: Record<string, TokenState>
  selectedToken: string
  portfolioAllocations: PortfolioAllocation[]

  // ─── Money Manager ─────────────────────────
  moneyManager: MoneyManagerSettings
  kellyPercent: number           // calculated Kelly %
  suggestedPositionSize: number  // calculated position size in USDT
  riskRewardRatio: number        // current R:R of open position

  // Actions
  setState: (data: Partial<TradingState>) => void
  setConnected: (connected: boolean, mode?: string) => void
  updateMoneyManager: (settings: Partial<MoneyManagerSettings>) => void
  toggleToken: (symbol: string) => void
  selectToken: (symbol: string) => void
  reset: () => void
}

const MAX_CHART_POINTS = 200

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

const defaultMoneyManager: MoneyManagerSettings = {
  riskPerTradePct: 2,
  maxConcurrentPositions: 8,
  maxCorrelatedPositions: 3,
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

const initialState = {
  isConnected: false,
  engineMode: 'disconnected' as const,
  isRunning: false,
  currentPrice: 0,
  // Aligned with PaperTradingEngine defaults (BTC/USDT, 'live')
  symbol: 'BTC/USDT',
  timeframe: 'live',
  exchange: 'MEXC',
  patternBuffer: [],
  entropy: 0,
  regime: '',
  latestSignal: null,
  signalsHistory: [],
  positions: [],
  // Match INITIAL_CAPITAL from paper-trading-engine.ts (10000 USDT)
  portfolioValue: 10000,
  cash: 10000,
  unrealizedPnl: 0,
  realizedPnl: 0,
  totalPnlPct: 0,
  exposurePct: 0,
  dailyReturnPct: 0,
  leverage: 3,
  autoMode: false,  // matches engine default (autoMode: false)
  circuitBreakers: { max_drawdown: false, daily_loss: false, volatility: false },
  isTradingAllowed: true,
  killSwitchActive: false,
  maxDrawdownPct: 0,
  dailyLossPct: 0,
  totalTrades: 0,
  winningTrades: 0,
  winRate: 0,
  maxDrawdown: 0,
  equityCurve: [10000],
  equityTimestamps: [Date.now() / 1000],
  monteCarlo: null,
  livingTrieStats: null,
  tradeHistory: [],
  candlesProcessed: 0,
  websocketStatus: 'disconnected',
  // Chart data
  entropyHistory: [] as EntropyPoint[],
  regimeHistory: [] as RegimePoint[],
  priceHistory: [] as PricePoint[],
  winRateHistory: [] as WinRatePoint[],
  confidenceHistory: [] as ConfidencePoint[],
  patternMatchHistory: [] as PatternMatchPoint[],
  learningStageHistory: [] as LearningStagePoint[],
  learningStage: 'BOOTSTRAP',
  driftDetected: false,
  lastRetrainTime: null as number | null,
  // Multi-token — all 50 supported tokens active by default (matches engine)
  activeTokens: [] as string[],  // engine will populate on first snapshot
  tokenStates: {} as Record<string, TokenState>,
  selectedToken: 'BTC/USDT',
  portfolioAllocations: [] as PortfolioAllocation[],
  // Money manager
  moneyManager: defaultMoneyManager,
  kellyPercent: 0,
  suggestedPositionSize: 0,
  riskRewardRatio: 0,
}

function trimArray<T>(arr: T[], max: number = MAX_CHART_POINTS): T[] {
  return arr.length > max ? arr.slice(-max) : arr
}

export const useTradingStore = create<TradingState>((set, get) => ({
  ...initialState,

  setState: (data) => set((state) => {
    // Auto-append to chart series if new scalar values are provided
    const now = Date.now() / 1000
    const updates: Partial<TradingState> = { ...data }

    if (data.entropy !== undefined && data.entropy > 0) {
      updates.entropyHistory = trimArray([
        ...state.entropyHistory,
        { time: now, value: data.entropy },
      ])
    }

    if (data.regime !== undefined && data.regime) {
      const regimeMap: Record<string, number> = {
        volatile: 0, ranging: 1, trending_down: 2, trending_up: 3,
      }
      updates.regimeHistory = trimArray([
        ...state.regimeHistory,
        { time: now, regime: data.regime, regimeNum: regimeMap[data.regime] ?? 1 },
      ])
    }

    if (data.currentPrice !== undefined && data.currentPrice > 0) {
      updates.priceHistory = trimArray([
        ...state.priceHistory,
        { time: now, price: data.currentPrice },
      ])
    }

    if (data.totalTrades !== undefined && data.totalTrades > 0) {
      updates.winRateHistory = trimArray([
        ...state.winRateHistory,
        {
          time: now,
          winRate: data.winRate ?? 0,
          trades: data.totalTrades,
          wins: data.winningTrades ?? 0,
        },
      ])
    }

    if (data.latestSignal) {
      updates.confidenceHistory = trimArray([
        ...state.confidenceHistory,
        {
          time: now,
          confidence: data.latestSignal.confidence,
          evScore: data.latestSignal.ev_score ?? 0,
          direction: data.latestSignal.direction,
        },
      ])
    }

    // Pattern match score (derived from entropy inverse)
    if (data.entropy !== undefined && data.entropy > 0) {
      const matchScore = 1 - data.entropy
      updates.patternMatchHistory = trimArray([
        ...state.patternMatchHistory,
        {
          time: now,
          matchScore,
          pathLength: data.patternBuffer?.length ?? 0,
        },
      ])
    }

    // Learning stage progression
    if (data.totalTrades !== undefined) {
      const t = data.totalTrades
      let stage = 'BOOTSTRAP'
      let stageNum = 0
      if (t >= 50) { stage = 'LEARNING'; stageNum = 1 }
      if (t >= 100) { stage = 'ADAPTING'; stageNum = 2 }
      if (t >= 200 && (data.winRate ?? 0) > 0.55) { stage = 'OPTIMIZED'; stageNum = 3 }
      if (t >= 300 && (data.winRate ?? 0) > 0.60) { stage = 'MATURE'; stageNum = 4 }

      updates.learningStage = stage
      updates.learningStageHistory = trimArray([
        ...state.learningStageHistory,
        {
          time: now,
          stage,
          stageNum,
          wr: data.winRate ?? 0,
          pf: data.winRate && data.winRate > 0 && data.winRate < 1
            ? (data.winRate * 2.5) / (1 - data.winRate)
            : 0,
        },
      ])
    }

    // Update multi-token states if tokenStates provided
    if (data.tokenStates) {
      updates.tokenStates = data.tokenStates
      // Compute portfolio allocations
      const tokens = Object.values(data.tokenStates) as TokenState[]
      const totalAllocated = tokens.reduce((sum, t) => sum + (t.isActive ? t.allocationPct : 0), 0)
      updates.portfolioAllocations = tokens
        .filter(t => t.isActive)
        .map(t => ({
          symbol: t.symbol,
          value: (state.portfolioValue * t.allocationPct) / 100,
          pct: totalAllocated > 0 ? (t.allocationPct / totalAllocated) * 100 : 0,
          pnl: t.realizedPnl + t.unrealizedPnl,
          color: TOKEN_COLORS[t.symbol] || '#6b7280',
        }))
    }

    // Compute Kelly and position size from money manager
    const mm = state.moneyManager
    const wr = data.winRate ?? state.winRate
    if (wr > 0 && wr < 1) {
      // Kelly % = W - (1-W) / R  where R = avg_win/avg_loss ≈ tpMultiplier
      const R = mm.takeProfitMultiplier
      const kelly = wr - ((1 - wr) / R)
      updates.kellyPercent = Math.max(0, kelly)
      updates.suggestedPositionSize = Math.max(0, kelly * mm.kellyFraction * state.portfolioValue)
      // Risk:reward ratio
      updates.riskRewardRatio = R
    }

    return { ...state, ...updates }
  }),

  setConnected: (connected, mode) =>
    set({
      isConnected: connected,
      engineMode: (mode as any) || (connected ? 'paper' : 'disconnected'),
    }),

  updateMoneyManager: (settings) =>
    set((state) => ({
      moneyManager: { ...state.moneyManager, ...settings },
    })),

  toggleToken: (symbol) =>
    set((state) => {
      const active = state.activeTokens.includes(symbol)
        ? state.activeTokens.filter(s => s !== symbol)
        : [...state.activeTokens, symbol]
      return { activeTokens: active }
    }),

  selectToken: (symbol) =>
    set({ selectedToken: symbol, symbol }),

  reset: () => set(initialState),
}))
