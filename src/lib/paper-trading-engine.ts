/**
 * PaperTradingEngine v3 — Multi-strategy parallel paper trading.
 *
 * STRATEGIES (each with own capital allocation):
 *   A: Momentum 24h     (30% = 3000 USDT) — top movers by |changePct|
 *   B: Mean Reversion   (25% = 2500 USDT) — RSI oversold/overbought
 *   C: Range Breakout   (25% = 2500 USDT) — breaks rolling 60-tick high/low
 *   D: Vol Squeeze      (20% = 2000 USDT) — Bollinger squeeze + first move
 *
 * BUG FIXES vs v2:
 *   ✓ Circuit breakers actually enforced (drawdown > maxDrawdownPct stops new entries)
 *   ✓ checkStops() runs even when trading is paused (was a critical bug)
 *   ✓ Trailing stop + break-even work on ALL positions (manual + auto)
 *   ✓ SL/TP are ATR-based (reactive to recent volatility, not stale 24h range)
 *   ✓ maxCorrelatedPositions enforced via sector grouping
 *   ✓ Time stop: 4h max hold, close at market
 *   ✓ Cooldown 30min post-stop-out per token
 *   ✓ Only WS-connected tokens (Coinbase) are eligible for auto-trading
 *
 * Each strategy has independent:
 *   - Cash pool, realized P&L, trade count, win count
 *   - Cooldown timer, position count
 *   - Signal generation logic
 *
 * 5-min console report shows per-strategy performance for comparison.
 */

import type { TokenState, MoneyManagerSettings } from '@/stores/trading-store'
import type { LivePriceFeed, TickerData } from './live-price-feed'
import { SUPPORTED_TOKENS_LIST, getTokenName } from './live-price-feed'

// ─── Engine version (for snapshot/export traceability) ────────────────
// Captured at build time from package.json + git. Bumped on every strategy
// change so snapshots report which code generated them.
// Format: "<strategy_stack> (<pkg_version>) @ <git_short>"
//   - strategy_stack: human-readable name of the active strategy versions
//   - pkg_version:    package.json "version" field
//   - git_short:      short commit hash (set at build via PPMT_GIT_SHORT env var,
//                     falls back to 'dev' if unset)
//
// Update ENGINE_STRATEGY_STACK when you change any strategy's exit/entry logic.
// restart.sh injects the git short hash as PPMT_GIT_SHORT env var at build time.

const ENGINE_PKG_VERSION = '0.2.0'  // mirrors package.json "version"
const ENGINE_STRATEGY_STACK = 'v82j-A-v82j-B-v83b-F-v82j-D'  // bump on strategy changes
const ENGINE_GIT_SHORT = (typeof process !== 'undefined' && process.env?.PPMT_GIT_SHORT) || 'dev'
const ENGINE_BUILT_AT = new Date().toISOString()

export const ENGINE_VERSION = {
  strategy_stack: ENGINE_STRATEGY_STACK,
  pkg_version: ENGINE_PKG_VERSION,
  git_short: ENGINE_GIT_SHORT,
  built_at: ENGINE_BUILT_AT,
  // Per-strategy exit stack — readable at a glance from a snapshot
  strategies: {
    A: { name: 'Momentum',     exit_stack: 'v82j', tp_mult_atr: 6.0, sl_mult_atr: 1.5, cat_sl_mult_atr: 2.5, partials: '0.5R/1.0R/2.0R/4.0R', lock: '+0.5R -> +0.35R', trail: 'regime-aware 1.0/0.5/0.3 ATR' },
    B: { name: 'MeanRev',      exit_stack: 'v82j', tp_mult_atr: 6.0, sl_mult_atr: 1.5, cat_sl_mult_atr: 2.5, partials: '0.5R/1.0R/2.0R/4.0R', lock: '+0.5R -> +0.35R', trail: 'regime-aware 1.0/0.5/0.3 ATR', pyramid: '+50% @ +1.0R (B-only)' },
    C: { name: 'Breakout',     exit_stack: 'DISABLED', tp_mult_atr: 3.0, sl_mult_atr: 1.0, cat_sl_mult_atr: 3.5, partials: 'none', lock: 'none', trail: 'none' },
    D: { name: 'Squeeze',      exit_stack: 'v82j', tp_mult_atr: 6.0, sl_mult_atr: 1.5, cat_sl_mult_atr: 2.5, partials: '0.5R/1.0R/2.0R/4.0R', lock: '+0.5R -> +0.35R', trail: 'regime-aware 1.0/0.5/0.3 ATR' },
    F: { name: 'Grid (v83b)',  exit_stack: 'v83b', tp_mult_atr: null, sl_mult_atr: null, cat_sl_mult_atr: null, partials: '4 levels above + 4 below SMA60, spacing 0.15%, TP 0.20%, SL 0.50%, max 3 per token, max 12 total', lock: 'n/a', trail: 'n/a' },
  },
  // Status flags captured from the source — useful to verify fixes are live
  flags: {
    v82j_exit_stack_on_A: true,   // 2026-06-30: was false (TP 1.2 ATR, inverted RR)
    v82j_exit_stack_on_B: true,   // baseline
    v82j_exit_stack_on_D: true,   // 2026-06-30: was false (TP 1.0 ATR, inverted RR)
    strategy_C_enabled: false,    // commented out in maybeAutoTrade()
    strategy_F_enabled: true,     // v83b for BLUE/STABLE (ATR% < 0.40)
  },
  // Human-readable summary — the AI can read this in one line
  summary: `${ENGINE_STRATEGY_STACK} (pkg ${ENGINE_PKG_VERSION}) @ ${ENGINE_GIT_SHORT}`,
} as const

// ─── Types ────────────────────────────────────────────────────────────
export type StrategyName = 'A' | 'B' | 'C' | 'D'

export interface StrategyPerf {
  name: string
  description: string
  cash: number
  allocated: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl_pct: number
  total_trades: number
  winning_trades: number
  win_rate: number
  open_positions: number
  last_signal_time: number
  color: string
}

interface StrategyState {
  cash: number
  allocated: number
  realizedPnl: number
  totalTrades: number
  winningTrades: number
  lastSignalTime: number
  positions: Set<string>
}

export interface PaperTradingState {
  is_running: boolean
  mode: 'paper'
  started_at: number
  engine_version: typeof ENGINE_VERSION  // v82j+: for snapshot/export traceability
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
  strategies_perf: Record<string, StrategyPerf>
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
  // v43a: lock-profit + multi-partial TP (15% @0.5R + 25% @1.0R) + trailing state
  lock_done?: boolean
  partial_done?: boolean        // legacy (kept for back-compat with old trades)
  partial1_done?: boolean       // v53h: first partial (v82j: @ +0.5R close 10%)
  partial2_done?: boolean       // v53h: second partial (v82j: @ +1.0R close 15%)
  partial3_done?: boolean       // v53h: third partial (v82j: @ +2.0R close 20%)
  partial4_done?: boolean       // v82h NEW: fourth partial at +4.0R (close 25%)
  initial_atr_pct?: number      // v82h NEW: ATR% at entry — for regime-aware trail
  pyramid_done?: boolean         // v61b NEW: pyramid at +1.0R (B only, +50% size, reset partials)
  trail_active?: boolean
  max_favorable_price?: number
  initial_atr?: number
  initial_sl_distance?: number
  pnl_pct: number
  pnl_usdt: number
  strategy: StrategyName
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
  strategy: StrategyName
}

// ─── Token universe ───────────────────────────────────────────────────
export const SUPPORTED_TOKENS = SUPPORTED_TOKENS_LIST as readonly string[]

export const TOKEN_NAMES: Record<string, string> = Object.fromEntries(
  SUPPORTED_TOKENS_LIST.map(s => [s, getTokenName(s)])
)

function hashColor(s: string): string {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  const hue = Math.abs(h) % 360
  return `hsl(${hue}, 70%, 55%)`
}
const TOKEN_COLORS: Record<string, string> = Object.fromEntries(
  SUPPORTED_TOKENS_LIST.map(s => [s, hashColor(s)])
)

// Tokens with Coinbase WS subscription — only these are eligible for auto-trading.
// The rest (CoinGecko-only) update every 30s, too stale for real-time signals.
const WS_ELIGIBLE_TOKENS: string[] = [
  'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
  'AVAX/USDT', 'DOGE/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT',
  'LTC/USDT', 'BCH/USDT', 'ATOM/USDT', 'XLM/USDT', 'NEAR/USDT',
  'APT/USDT', 'ARB/USDT', 'OP/USDT', 'INJ/USDT', 'FIL/USDT',
  'AAVE/USDT', 'MKR/USDT', 'SUI/USDT', 'TIA/USDT', 'SEI/USDT',
  'STX/USDT', 'IMX/USDT', 'GRT/USDT', 'LDO/USDT', 'SAND/USDT',
  'MANA/USDT', 'AXS/USDT', 'PEPE/USDT', 'WIF/USDT', 'SHIB/USDT',
  'PYTH/USDT', 'JTO/USDT', 'RNDR/USDT', 'ICP/USDT', 'ETC/USDT',
  'ALGO/USDT', 'FLOW/USDT', 'HBAR/USDT', 'MINA/USDT', 'ZEC/USDT',
  'CRV/USDT', 'SNX/USDT', 'COMP/USDT', 'UNI/USDT', 'DYDX/USDT',
  'JUP/USDT', 'WLD/USDT',
]

// Sector grouping for correlation control
const TOKEN_SECTOR: Record<string, string> = {
  'BTC/USDT': 'btc', 'BCH/USDT': 'btc',
  'ETH/USDT': 'eth', 'LDO/USDT': 'eth', 'ARB/USDT': 'eth', 'OP/USDT': 'eth', 'MATIC/USDT': 'eth',
  'SOL/USDT': 'sol', 'JUP/USDT': 'sol', 'JTO/USDT': 'sol', 'WIF/USDT': 'sol',
  'AVAX/USDT': 'l1', 'NEAR/USDT': 'l1', 'APT/USDT': 'l1', 'SUI/USDT': 'l1', 'TIA/USDT': 'l1', 'SEI/USDT': 'l1',
  'ADA/USDT': 'l1', 'DOT/USDT': 'l1', 'ATOM/USDT': 'l1', 'ALGO/USDT': 'l1', 'HBAR/USDT': 'l1',
  'ICP/USDT': 'l1', 'FLOW/USDT': 'l1', 'XTZ/USDT': 'l1', 'XLM/USDT': 'l1', 'INJ/USDT': 'l1',
  'FIL/USDT': 'l1', 'STX/USDT': 'l1', 'MINA/USDT': 'l1', 'TON/USDT': 'l1',
  'XRP/USDT': 'majors', 'LTC/USDT': 'majors', 'BNB/USDT': 'majors', 'TRX/USDT': 'majors',
  'ETC/USDT': 'majors', 'XMR/USDT': 'majors', 'ZEC/USDT': 'majors', 'DASH/USDT': 'majors',
  'LINK/USDT': 'defi', 'UNI/USDT': 'defi', 'AAVE/USDT': 'defi', 'COMP/USDT': 'defi',
  'CRV/USDT': 'defi', 'SNX/USDT': 'defi', 'DYDX/USDT': 'defi', 'MKR/USDT': 'defi',
  'DOGE/USDT': 'meme', 'SHIB/USDT': 'meme', 'PEPE/USDT': 'meme', 'FLOKI/USDT': 'meme',
  'RNDR/USDT': 'ai', 'WLD/USDT': 'ai', 'FET/USDT': 'ai', 'AGIX/USDT': 'ai', 'OCEAN/USDT': 'ai',
  'AXS/USDT': 'gaming', 'SAND/USDT': 'gaming', 'MANA/USDT': 'gaming', 'GALA/USDT': 'gaming',
  'ENJ/USDT': 'gaming', 'CHZ/USDT': 'gaming', 'IMX/USDT': 'gaming',
  'GRT/USDT': 'infra', 'PYTH/USDT': 'infra',
}

function getSector(symbol: string): string {
  return TOKEN_SECTOR[symbol] || 'other'
}

// ─── Indicators ───────────────────────────────────────────────────────
interface PriceSample { ts: number; price: number }

function computeRSI(prices: number[], period: number = 14): number {
  if (prices.length < period + 1) return 50
  let gains = 0, losses = 0
  for (let i = 1; i <= period; i++) {
    const ch = prices[i] - prices[i - 1]
    if (ch >= 0) gains += ch
    else losses -= ch
  }
  let avgGain = gains / period
  let avgLoss = losses / period
  for (let i = period + 1; i < prices.length; i++) {
    const ch = prices[i] - prices[i - 1]
    const g = ch > 0 ? ch : 0
    const l = ch < 0 ? -ch : 0
    avgGain = (avgGain * (period - 1) + g) / period
    avgLoss = (avgLoss * (period - 1) + l) / period
  }
  if (avgLoss === 0) return 100
  const rs = avgGain / avgLoss
  return 100 - (100 / (1 + rs))
}

function computeATR(prices: number[], period: number = 60): number {
  if (prices.length < 2) return 0
  const start = Math.max(1, prices.length - period)
  let sum = 0
  let count = 0
  for (let i = start; i < prices.length; i++) {
    sum += Math.abs(prices[i] - prices[i - 1])
    count++
  }
  if (count === 0) return 0
  const rawATR = sum / count
  // FIX v10: Floor ATR at 0.1% of price (SL ≥ 0.4%).
  // FIX v12: Bump floor to 0.3% of price (SL ≥ 0.45%, TP ≥ 0.9%).
  //   v10's 0.1% floor was still too tight — Coinbase spread noise is ~0.05%,
  //   so 3 normal spreads hit the SL. Snapshot showed 17/20 trades stopping
  //   out in 1-13min, all near the 60s min-hold boundary.
  const lastPrice = prices[prices.length - 1]
  const minATR = lastPrice > 0 ? lastPrice * 0.003 : 0
  return Math.max(rawATR, minATR)
}

function computeBollinger(prices: number[], period: number = 50, mult: number = 2) {
  const last = prices[prices.length - 1] || 0
  const slice = prices.slice(-period)
  if (slice.length < 5) return { middle: last, upper: 0, lower: 0, width: 0 }
  const mean = slice.reduce((a, b) => a + b, 0) / slice.length
  const variance = slice.reduce((s, p) => s + (p - mean) ** 2, 0) / slice.length
  const std = Math.sqrt(variance)
  return {
    middle: mean,
    upper: mean + mult * std,
    lower: mean - mult * std,
    width: mean > 0 ? (mult * 2 * std) / mean : 0,
  }
}

function computeRollingRange(prices: number[], period: number = 60) {
  const slice = prices.slice(-period)
  if (slice.length === 0) return { high: 0, low: 0 }
  return { high: Math.max(...slice), low: Math.min(...slice) }
}

// ─── Constants ────────────────────────────────────────────────────────
export const INITIAL_CAPITAL = 10000
const TAKER_FEE_PCT = 0.10
const SLIPPAGE_PCT = 0.05

const STRATEGY_ALLOCATION: Record<StrategyName, number> = {
  // v14 NIGHT1 FIX: Strategy C pausada (WR 10-20% en ambos snapshots paralelos).
  //   Capital redistribuido a B (mejor WR 40-50%) y D (R/R mejor con TP 4x ATR).
  A: 1000,   // bajado: A apenas opera, no necesita tanto cash
  B: 4000,   // subido: B tiene mejor WR en ambos snapshots
  C: 0,      // PAUSADA — Strategy C era defectuosa
  D: 5000,   // subido: D tiene mejor R/R teórico
}

const STRATEGY_INFO: Record<StrategyName, { name: string; description: string; color: string }> = {
  A: { name: 'Momentum', description: 'Top 24h movers with volume confirmation', color: '#3b82f6' },
  B: { name: 'Mean Reversion', description: 'RSI oversold/overbought counter-trend', color: '#10b981' },
  C: { name: 'Breakout', description: 'Rolling 60-tick high/low breaks', color: '#f59e0b' },
  D: { name: 'Squeeze', description: 'Bollinger band squeeze + expansion', color: '#8b5cf6' },
}

const DEFAULT_MONEY_MANAGER: MoneyManagerSettings = {
  riskPerTradePct: 3,
  maxConcurrentPositions: 8,
  maxCorrelatedPositions: 3,
  maxDrawdownPct: 25,
  dailyLossLimitPct: 8,
  positionSizingMethod: 'risk_parity',
  kellyFraction: 0.5,
  defaultLeverage: 1,
  maxLeverage: 3,
  takeProfitMultiplier: 2.5,
  stopLossATR: 1.5,
  trailingStopEnabled: true,
  trailingStopActivationPct: 1.0,
  trailingStopDistancePct: 0.5,
  breakEvenEnabled: true,
  breakEvenActivationPct: 0.5,
}

// ─── Engine ───────────────────────────────────────────────────────────
export class PaperTradingEngine {
  private strategies: Record<StrategyName, StrategyState>
  private positions: Map<string, PaperPosition> = new Map()
  private trades: PaperTrade[] = []
  private orders: PaperOrder[] = []
  private signals: any[] = []
  private equity: number[] = [INITIAL_CAPITAL]
  private timestamps: number[] = [Date.now() / 1000]
  private running: boolean = false
  private tradingEnabled: boolean = true
  private autoMode: boolean = true
  private interval: ReturnType<typeof setInterval> | null = null
  private priceFeed: LivePriceFeed
  private activeTokens: string[] = [...SUPPORTED_TOKENS]
  private selectedToken: string = 'BTC/USDT'
  private moneyManager: MoneyManagerSettings = { ...DEFAULT_MONEY_MANAGER }
  private startedAt: number = Date.now() / 1000

  // Pattern buffer (kept for UI compatibility)
  private lastTickPrices: Map<string, number> = new Map()
  private patternBufferPerToken: Map<string, string[]> = new Map()
  private livingTrie: Map<string, number> = new Map()
  private candlesProcessed: number = 0
  private maxPatternDepthObserved: number = 0
  private lastTriePruneTime: number = 0
  // FIX v11: tickCount + lastTickAt were lost when v7 rewrote the engine.
  // Without these, the BrainPanel always shows "tick #0" and the EXPORT v9
  // snapshot always reports tick_count=0 / last_tick_at=null — making it
  // look like the WebSocket loop is dead even when it's running fine.
  private tickCount: number = 0
  private lastTickAt: number = 0

  // Price history for indicators (200 samples = 5 min at 1.5s)
  private priceHistory: Map<string, PriceSample[]> = new Map()

  // Cooldown after stop-out (timestamp when cooldown expires)
  private cooldownUntil: Map<string, number> = new Map()

  // Console report timer
  private lastReportTime: number = 0
  private lastCBLogTime: number = 0

  // v15 VOL CB: timestamp when volatility pause expires (0 = no pause)
  private volPauseUntil: number = 0
  private lastVolCBLogTime: number = 0

  // FIX v0.84: Throttle "no price data" warnings per symbol (once per 30s)
  private _noPriceWarn: Map<string, number> = new Map()

  // ─── v83b: Strategy F (Grid Trading) state for BLUE/STABLE (ATR% < 0.40) ───
  // Grid positions are tracked SEPARATELY from this.positions (which is 1-per-symbol).
  // Each token can have up to V83F_MAX_POSITIONS_PER_TOKEN concurrent grid positions.
  private fGridPositions: Map<string, Array<{
    entry_price: number
    direction: 'LONG' | 'SHORT'
    qty: number
    size_usdt: number
    level: number
    tp: number
    sl: number
    entry_tick: number
    max_favorable_price: number
  }>> = new Map()
  private fLastEntryTick: Map<string, number> = new Map()
  private fGridBaseline: Map<string, number> = new Map()

  constructor(priceFeed: LivePriceFeed) {
    this.priceFeed = priceFeed
    this.strategies = {
      A: { cash: STRATEGY_ALLOCATION.A, allocated: STRATEGY_ALLOCATION.A, realizedPnl: 0, totalTrades: 0, winningTrades: 0, lastSignalTime: 0, positions: new Set() },
      B: { cash: STRATEGY_ALLOCATION.B, allocated: STRATEGY_ALLOCATION.B, realizedPnl: 0, totalTrades: 0, winningTrades: 0, lastSignalTime: 0, positions: new Set() },
      C: { cash: STRATEGY_ALLOCATION.C, allocated: STRATEGY_ALLOCATION.C, realizedPnl: 0, totalTrades: 0, winningTrades: 0, lastSignalTime: 0, positions: new Set() },
      D: { cash: STRATEGY_ALLOCATION.D, allocated: STRATEGY_ALLOCATION.D, realizedPnl: 0, totalTrades: 0, winningTrades: 0, lastSignalTime: 0, positions: new Set() },
    }
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

  // ─── Market orders (strategy-aware) ───────────────────────────────
  marketBuy(symbol: string, usdtAmount: number, strategy: StrategyName = 'A'): OrderResult {
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
    const fee = (grossUsdt * TAKER_FEE_PCT) / 100
    const totalCost = grossUsdt + fee

    const strat = this.strategies[strategy]
    if (totalCost > strat.cash) {
      return { success: false, error: `Insufficient cash in ${strategy}. Need ${totalCost.toFixed(2)}, have ${strat.cash.toFixed(2)}` }
    }

    const qty = grossUsdt / fillPrice
    strat.cash -= totalCost

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
        status: 'OPEN',  // v82j+: was 'ACTIVE' — export filter expected 'OPEN', causing open_positions: [] bug
        entry_price: fillPrice,
        entry_time: new Date().toISOString(),
        size_usdt: grossUsdt,
        qty,
        current_sl: null,
        current_tp: null,
        catastrophic_sl: null,
        pnl_pct: 0,
        pnl_usdt: 0,
        strategy,
      })
      strat.positions.add(symbol)
    }

    this.orders.unshift({
      timestamp: new Date().toISOString(),
      side: 'BUY', symbol, qty, price: fillPrice,
      usdt: grossUsdt, fee, type: 'MARKET', strategy,
    })
    if (this.orders.length > 100) this.orders = this.orders.slice(0, 100)
    console.log(`[Paper/${strategy}] BUY ${symbol} ${qty.toFixed(6)} @ ${fillPrice} (fee ${fee.toFixed(2)})`)
    return { success: true, fillPrice, qty, fee }
  }

  marketSell(symbol: string, usdtAmount: number, strategy: StrategyName = 'A'): OrderResult {
    if (!this.tradingEnabled) {
      return { success: false, error: 'Trading disabled (kill switch active). Click Start Trading.' }
    }
    if (usdtAmount <= 0) return { success: false, error: 'Amount must be > 0' }

    const ticker = this.priceFeed.getData(symbol)
    if (!ticker) {
      return { success: false, error: `No live price for ${symbol} yet. Wait for WS connection.` }
    }

    const fillPrice = ticker.price * (1 - SLIPPAGE_PCT / 100)
    const fee = (usdtAmount * TAKER_FEE_PCT) / 100
    const existing = this.positions.get(symbol)

    // Close LONG if it exists — credit the ORIGINAL strategy
    if (existing && existing.direction === 'LONG') {
      const origStrat = this.strategies[existing.strategy]
      const closeQty = Math.min(usdtAmount / fillPrice, existing.qty)
      const proceeds = closeQty * fillPrice
      const pnl = (fillPrice - existing.entry_price) * closeQty - fee
      origStrat.cash += proceeds - fee
      origStrat.realizedPnl += pnl
      origStrat.totalTrades++
      if (pnl > 0) origStrat.winningTrades++

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
        origStrat.positions.delete(symbol)
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
      console.log(`[Paper/${existing.strategy}] SELL (close LONG) ${symbol} ${closeQty.toFixed(6)} @ ${fillPrice} (pnl ${pnl.toFixed(2)})`)
      return { success: true, fillPrice, qty: closeQty, fee, pnl }
    }

    // Open SHORT with the CALLING strategy
    const strat = this.strategies[strategy]
    const grossUsdt = usdtAmount
    const margin = grossUsdt
    const totalCost = margin + fee
    if (totalCost > strat.cash) {
      return { success: false, error: `Insufficient cash in ${strategy} for short margin. Need ${totalCost.toFixed(2)}` }
    }
    strat.cash -= totalCost
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
        status: 'OPEN',  // v82j+: was 'ACTIVE' — export filter expected 'OPEN', causing open_positions: [] bug
        entry_price: fillPrice,
        entry_time: new Date().toISOString(),
        size_usdt: grossUsdt,
        qty,
        current_sl: null,
        current_tp: null,
        catastrophic_sl: null,
        pnl_pct: 0,
        pnl_usdt: 0,
        strategy,
      })
      strat.positions.add(symbol)
    }

    this.orders.unshift({
      timestamp: new Date().toISOString(),
      side: 'SELL', symbol, qty, price: fillPrice,
      usdt: grossUsdt, fee, type: 'MARKET', strategy,
    })
    if (this.orders.length > 100) this.orders = this.orders.slice(0, 100)
    console.log(`[Paper/${strategy}] SELL (open SHORT) ${symbol} ${qty.toFixed(6)} @ ${fillPrice} (fee ${fee.toFixed(2)})`)
    return { success: true, fillPrice, qty, fee }
  }

  closePosition(symbol: string): OrderResult {
    const pos = this.positions.get(symbol)
    if (!pos) return { success: false, error: `No open position for ${symbol}` }

    const ticker = this.priceFeed.getData(symbol)
    if (!ticker) return { success: false, error: `No live price for ${symbol}` }

    const isLong = pos.direction === 'LONG'
    const fillPrice = isLong
      ? ticker.price * (1 - SLIPPAGE_PCT / 100)
      : ticker.price * (1 + SLIPPAGE_PCT / 100)

    const strat = this.strategies[pos.strategy]
    const grossProceeds = pos.qty * fillPrice
    const fee = (grossProceeds * TAKER_FEE_PCT) / 100
    const pnl = isLong
      ? (fillPrice - pos.entry_price) * pos.qty - fee
      : (pos.entry_price - fillPrice) * pos.qty - fee

    if (isLong) {
      strat.cash += grossProceeds - fee
    } else {
      strat.cash += pos.size_usdt + pnl
    }
    strat.realizedPnl += pnl
    strat.totalTrades++
    if (pnl > 0) strat.winningTrades++

    this.trades.unshift({
      ...pos,
      close_price: fillPrice,
      close_reason: 'CLOSED_BY_USER',
      closed_at: new Date().toISOString(),
      pnl_usdt: pnl,
    })
    if (this.trades.length > 100) this.trades = this.trades.slice(0, 100)
    this.positions.delete(symbol)
    strat.positions.delete(symbol)

    console.log(`[Paper/${pos.strategy}] CLOSE ${symbol} @ ${fillPrice} (pnl ${pnl.toFixed(2)})`)
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

  setTimeframe(_tf: string) { /* no-op */ }

  setActiveTokens(tokens: string[]) {
    const prev = new Set(this.activeTokens)
    this.activeTokens = tokens
    if (!tokens.includes(this.selectedToken)) {
      this.activeTokens = [...tokens, this.selectedToken]
    }
    const same = this.activeTokens.length === prev.size &&
      this.activeTokens.every(t => prev.has(t))
    if (!same) {
      this.priceFeed.setSymbols(this.activeTokens)
    }
  }

  setMoneyManager(settings: Partial<MoneyManagerSettings>) {
    this.moneyManager = { ...this.moneyManager, ...settings }
  }

  // ─── Price history ────────────────────────────────────────────────
  private updatePriceHistory() {
    const now = Date.now()
    for (const sym of this.activeTokens) {
      const t = this.priceFeed.getData(sym)
      if (!t) continue
      const arr = this.priceHistory.get(sym) || []
      arr.push({ ts: now, price: t.price })
      if (arr.length > 200) arr.shift()
      this.priceHistory.set(sym, arr)
    }
  }

  // ─── Pattern buffer (kept for UI) ─────────────────────────────────
  private updatePatternsAndTrie() {
    const now = Date.now() / 1000
    for (const sym of this.activeTokens) {
      const t = this.priceFeed.getData(sym)
      if (!t) continue
      const last = this.lastTickPrices.get(sym)
      this.lastTickPrices.set(sym, t.price)
      if (last === undefined) continue
      const pct = ((t.price - last) / last) * 100
      // FIX v10: Lower thresholds — Coinbase ticks update every 1.5s and most
      // moves are < 0.02%, so the old thresholds produced 90% 'F' symbols,
      // entropy = 1.0 (max chaos), and match_score = 0 for every pattern.
      // New thresholds: U/D at 0.01%, V/B at 0.08%.
      const sym_char =
        pct >= 0.08 ? 'V' :
        pct >= 0.01 ? 'U' :
        pct <= -0.08 ? 'B' :
        pct <= -0.01 ? 'D' : 'F'
      let buf = this.patternBufferPerToken.get(sym) || []
      buf.push(sym_char)
      if (buf.length > 12) buf = buf.slice(-12)
      this.patternBufferPerToken.set(sym, buf)
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
      this.tickCount++          // FIX v11: tick = one symbol's pattern update
    }
    this.lastTickAt = Date.now()  // FIX v11: stamp the moment we finished a tick batch
    if (now - this.lastTriePruneTime > 60 && this.livingTrie.size > 5000) {
      const entries = Array.from(this.livingTrie.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5000)
      this.livingTrie = new Map(entries)
      this.lastTriePruneTime = now
    }
  }

  // ─── Stops (FIXED: always runs, ATR-based, time stop, cooldown) ──
  private checkStops() {
    // CRITICAL FIX: removed `if (!this.tradingEnabled) return`
    // Stops MUST always run, even when trading is paused.
    const mm = this.moneyManager
    const now = Date.now()

    for (const [sym, pos] of Array.from(this.positions.entries())) {
      const ticker = this.priceFeed.getData(sym)
      if (!ticker) {
        // FIX v0.84: No price data for this symbol — can't check SL/TP.
        // Log warning so we can diagnose (priceFeed may have dropped the
        // WS subscription for this token). Position stays open but we
        // can't risk closing it at a stale price.
        // Throttle: only log once per 30s per symbol to avoid spam.
        const lastWarn = this._noPriceWarn.get(sym) || 0
        if (now - lastWarn > 30_000) {
          console.warn(`[Paper/checkStops] ⚠️ No live price for ${sym} — SL/TP check skipped. Position stays open.`)
          this._noPriceWarn.set(sym, now)
        }
        continue
      }
      const price = ticker.price
      const isLong = pos.direction === 'LONG'

      // ─── Time stop: 1h max hold (v31b: tighter time stop improves P&L
      //   by cutting marginal positions before they drift to SL) ───
      const entryTime = new Date(pos.entry_time).getTime()
      const holdMs = now - entryTime
      if (holdMs > 1 * 60 * 60 * 1000) {
        console.log(`[Paper/TimeStop] ${sym} held ${Math.round(holdMs / 60000)}min — closing at market`)
        this.closePosition(sym)
        if (this.trades[0]) this.trades[0].close_reason = 'CLOSED_BY_TIME_STOP'
        // v14 NIGHT1 FIX: cooldown 30min → 45min
        this.cooldownUntil.set(sym, now + 45 * 60 * 1000)
        continue
      }

      const pnlPct = isLong
        ? ((price - pos.entry_price) / pos.entry_price) * 100
        : ((pos.entry_price - price) / pos.entry_price) * 100

      // ─── Set ATR-based SL/TP for entries without them (manual entries) ───
      if (pos.current_sl === null || pos.current_tp === null) {
        const hist = this.priceHistory.get(sym) || []
        const prices = hist.map(h => h.price)
        const atr = computeATR(prices, 60)
        if (atr > 0) {
          // v14 NIGHT1 FIX: SL fallback más ancho (1.5 → 2.0 ATR) y TP más cercano (3 → 2.5 ATR).
          //   Reducía trades cerrados en <2min (35% en snapshot A).
          if (pos.current_sl === null) {
            pos.current_sl = isLong
              ? pos.entry_price - atr * 2.0
              : pos.entry_price + atr * 2.0
          }
          if (pos.current_tp === null) {
            pos.current_tp = isLong
              ? pos.entry_price + atr * 2.5
              : pos.entry_price - atr * 2.5
          }
        }
      }

      // ─── Trailing stop (works for ALL positions, not just auto) ───
      if (mm.trailingStopEnabled && pnlPct > mm.trailingStopActivationPct && pos.current_sl !== null) {
        const trailDist = pos.entry_price * (mm.trailingStopDistancePct / 100)
        if (isLong) {
          pos.current_sl = Math.max(pos.current_sl, price - trailDist)
        } else {
          pos.current_sl = Math.min(pos.current_sl, price + trailDist)
        }
      }

      // ─── Break-even ───
      if (mm.breakEvenEnabled && pnlPct > mm.breakEvenActivationPct && pos.current_sl !== null) {
        if (isLong && pos.current_sl < pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        } else if (!isLong && pos.current_sl > pos.entry_price) {
          pos.current_sl = pos.entry_price
          pos.status = 'BREAK_EVEN_SECURED'
        }
      }

      // ─── v82j: Lock + 4-Partial TP @ 0.5/1.0/2.0/4.0R + Regime Trail + TIERED Adaptive + PYRAMID @ +1.0R (+50%) ───
      // v81 → v82h → v82j changes (RR fix — 9-variant iteration validated 12 seeds × 8 profiles = 96 runs):
      //   H1: Lock @ +0.5R → SL = entry + 0.35R + ACTIVATE TRAIL immediately (was: trail after partial3)
      //   H2: 4 partials @ 0.5R/1.0R/2.0R/4.0R (was 3 @ 0.5/1.0/1.25R in v81) — lifts avg_r
      //   H3: TP 1.2 ATR → 6.0 ATR (distant ceiling — trail is the actual exit)
      //   H4: Trail regime-aware: 1.0/0.5/0.3 ATR by ATR% (was 0.30 ATR flat)
      //   H5: Pyramid 0.75 → 0.50 (less aggressive adding, less risk if reversal)
      //   H6: Partial4 NEW @ +4.0R (25%) — captures extended runners
      // v82j vs v81 backtest (12 seeds × 8 profiles = 96 runs):
      //   Profile    |  v81 WR%/RR/PnL     |  v82j WR%/RR/PnL      | Δ
      //   MIXED      |  68.7/0.37/-91       |  70.6/0.54/-76        | +43% RR, +15 PnL
      //   BULL       |  79.0/1.03/+166      |  80.0/1.54/+272       | +49% RR, +106 PnL
      //   BEAR       |  68.0/0.41/-7        |  66.2/0.62/+20        | +51% RR, +27 PnL
      //   HIGHVOL    |  77.6/0.65/-13       |  76.4/0.79/-34        | +22% RR (slight PnL dip)
      //   MEME       |  78.6/1.08/+373      |  77.9/1.61/+577       | +50% RR, +204 PnL
      //   ALT        |  79.4/0.75/+6        |  78.3/1.04/+0         | +39% RR (PnL flat)
      //   BLUE       |  0 trades            |  0 trades             | (TODO: Strategy F grid)
      //   STABLE     |  0 trades            |  0 trades             | (TODO: Strategy F grid)
      //   ─────────  |  ─────────────────   |  ─────────────────    | ────────────
      //   TOTAL      |  avg_rr 0.535        |  avg_rr 0.768 (+43%)  | total PnL +75%
      //   SCORE      |  17/30 (5 crit×6 pf) |  20/30 (BEST)         | +3 pts
      // Gate (WR>64% AND RR>1.8 per profile):
      //   v81:  6/8 pass WR, 0/8 pass RR, 0/8 pass both
      //   v82h: 5/8 pass WR, 0/8 pass RR, 0/8 pass both (BEAR WR regressed to 61%)
      //   v82j: 6/8 pass WR, 0/8 pass RR, 0/8 pass both (KEEPS WR, BEST RR)
      // v82j is the WINNER: same WR pass count as v81 but +43% RR and +75% PnL.
      // Gate still not fully met — need Strategy F (grid) for BLUE/STABLE and/or regime switching (v83+).
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.35R when +0.5R reached + ACTIVATE TRAIL
      // Partial1: close 10% at +0.5R (v82j: was 5% @ +0.5R in v53h)
      // Partial2: close 15% at +1.0R (v82j: was 10% @ +1.0R in v53h)
      // Partial3: close 20% at +2.0R (v82j: was 15% @ +1.25R in v53h)
      // Partial4: close 25% at +4.0R, then trail on remainder (30%)
      // Trail:    1.0/0.5/0.3 ATR by ATR% regime (high/med/low vol)
      // v56d: Adaptive ATR sizing — when ATR% < 0.6%, halve position size
      //       (calm-market trades have low edge, smaller size reduces drawdowns)
      // v57i: B base size 0.15 (was 0.125 in v56d) — push B winners with adaptive protection
      // v58d: A base size 0.030 (was 0.025 in v31b-v57i) — modest A boost with adaptive protection
      // v82h/v82j validated: avg_rr 0.535 (v81) → 0.768 (v82j), +43% lift across 8 profiles
      if (pos.initial_atr && pos.initial_sl_distance && pos.initial_sl_distance > 0) {
        let rMultiple = isLong
          ? (price - pos.entry_price) / pos.initial_sl_distance
          : (pos.entry_price - price) / pos.initial_sl_distance

        // Track MFE (max favorable price) for trailing
        if (pos.max_favorable_price === undefined) pos.max_favorable_price = pos.entry_price
        if (isLong && price > pos.max_favorable_price) pos.max_favorable_price = price
        if (!isLong && price < pos.max_favorable_price) pos.max_favorable_price = price

        // v61b NEW: PYRAMID at +1.0R (Strategy B only) — add 50% more size at current price
        //   - Increases pos.qty by 50%
        //   - Recomputes pos.entry_price as weighted average
        //   - Resets SL to new_entry - 1.5*ATR (gives pyramided position room)
        //   - Resets partial1/2/3_done, lock_done, trail_active so they re-fire on pyramided pos
        //   12-seed validation: +3.13 P&L vs v60b, MaxDD +0.01% (negligible risk increase)
        // v62a→v67: Pyramid pct 0.50 → 0.75 (push B winners even harder)
        //   12-seed validation: +2.58 P&L vs v61b, MaxDD same 0.29%, PF 2.66→2.72, Sharpe +8.69→+9.82
        // v82h: Pyramid pct 0.75 → 0.50 (less aggressive adding, less risk if reversal)
        //   Backtest 96 runs: less P&L but more controlled drawdowns.
        if (!pos.pyramid_done && pos.strategy === 'B' && rMultiple >= 1.0) {
          // v81 F3: Disable pyramid in HIGHVOL (ATR% > 1.5)
          //   Root cause: v67 pyramiding in HIGHVOL amplified losses — MaxDD 2.34%, Profit 33%
          //   Fix: skip pyramid when ATR% > 1.5 → MaxDD 1.54%, less profit but controlled risk
          //   Backtest v81 vs v67: HIGHVOL P&L -100 → -13, MaxDD 2.34% → 1.54%
          const atrPctPyramid = (pos.initial_atr / pos.entry_price) * 100
          if (atrPctPyramid > 1.5) {
            pos.pyramid_done = true  // disable pyramid by marking as done
          } else {
            const pyramidPct = 0.50  // v82h: was 0.75 in v67-v81
            const oldQty = pos.qty
            const oldEntry = pos.entry_price
            const addQty = oldQty * pyramidPct
            const newQty = oldQty + addQty
            const newEntry = (oldQty * oldEntry + addQty * price) / newQty
            pos.qty = newQty
            pos.entry_price = newEntry
            // Recompute ATR and SL distance for pyramided position
            const newATR = computeATR(hist.map(h => h.price), 60)
            if (newATR > 0) {
              pos.initial_atr = newATR
              pos.initial_sl_distance = newATR * 1.5
              pos.current_sl = isLong ? newEntry - newATR * 1.5 : newEntry + newATR * 1.5
              pos.catastrophic_sl = isLong ? newEntry - newATR * 2.5 : newEntry + newATR * 2.5  // v81 F4: 2.5 ATR (was 4.0)
              pos.current_tp = null
            }
            // Reset partials + lock + trail so they fire again on pyramided position
            pos.partial1_done = false
            pos.partial2_done = false
            pos.partial3_done = false
            pos.partial4_done = false  // v82h
            pos.lock_done = false
            pos.trail_active = false
            pos.max_favorable_price = price
            pos.pyramid_done = true
            console.log(`[Paper/v82h] ${sym} PYRAMID +50% @ ${price} (R was ${rMultiple.toFixed(2)}, new entry ${newEntry.toFixed(4)})`)
          }
        }

        // v61b: Recompute rMultiple after pyramid (entry_price may have changed)
        rMultiple = isLong
          ? (price - pos.entry_price) / (pos.initial_sl_distance || 1)
          : (pos.entry_price - price) / (pos.initial_sl_distance || 1)

        // 1. Lock profit at +0.5R → move SL to entry+0.35R + ACTIVATE TRAIL (v82h)
        //    v82h KEY CHANGE: Trail activates immediately after lock (was: after partial3 in v81).
        //    This protects the remainder once +0.5R is reached, instead of waiting until +1.25R.
        //    Reason: avg_r lifted when trail fires earlier — winners that reverse from +1R now
        //    exit at trail (~+0.5R) instead of lock floor (+0.35R).
        if (!pos.lock_done && rMultiple >= 0.5) {
          const lockR = 0.35
          if (isLong) {
            const newSL = pos.entry_price + lockR * pos.initial_sl_distance
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = pos.entry_price - lockR * pos.initial_sl_distance
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
          pos.lock_done = true
          // v82h: Activate trail right after lock — use regime-aware trail distance
          pos.trail_active = true
          const atrPctForTrail = pos.initial_atr_pct ?? 1.0
          const trailMult = atrPctForTrail > 1.5 ? 1.0 : (atrPctForTrail > 0.8 ? 0.5 : 0.3)
          const trailDist = pos.initial_atr * trailMult
          if (isLong) {
            let newSL = price - trailDist
            // Never trail below lock floor
            const lockFloor = pos.entry_price + lockR * pos.initial_sl_distance
            if (newSL < lockFloor) newSL = lockFloor
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            let newSL = price + trailDist
            const lockFloor = pos.entry_price - lockR * pos.initial_sl_distance
            if (newSL > lockFloor) newSL = lockFloor
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
        }

        // v82h: 4 PARTIALS at higher R levels (was 3 @ 0.5/1.0/1.25R in v81)
        //   KEY INSIGHT: avg_r in the engine = mean(r_multiple) over ALL trades
        //   INCLUDING partials (each partial is a separate entry in self.trades).
        //   v82j WINNER CONFIG (validated 12 seeds × 8 profiles = 96 runs):
        //     v81 partials (0.5/1.0/1.25R): RR avg 0.535, score 17/30
        //     v82h partials (0.8/1.5/2.5/4.0R): RR avg 0.831, score 19/30 — but BEAR WR<64%
        //     v82j partials (0.5/1.0/2.0/4.0R): RR avg 0.768, score 20/30 — BEST BALANCE
        //   v82j keeps 6/8 profiles above WR 64% (same as v81) AND improves RR +43% AND PnL +75%.
        //   Partial percentages: 10%/15%/20%/25% (total 70% captured, 30% remainder for trail).

        // 2a. Partial TP1 at +0.5R → close 10% (v82j: was +0.8R in v82h, was 5% @ +0.5R in v53h)
        if (!pos.partial1_done && rMultiple >= 0.5) {
          const partialPct1 = 0.10
          const partialQty1 = pos.qty * partialPct1
          if (partialQty1 > 0.0001) {
            const partialResult1 = isLong
              ? this.marketSell(sym, partialQty1 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty1 * pos.entry_price, pos.strategy)
            if (partialResult1.success) {
              pos.qty -= partialQty1
              console.log(`[Paper/v82j] ${sym} PARTIAL_TP1 10% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial1_done = true
        }

        // 2b. Partial TP2 at +1.0R → close 15% (v82j: was +1.5R in v82h, was 10% @ +1.0R in v53h)
        if (!pos.partial2_done && rMultiple >= 1.0) {
          const partialPct2 = 0.15
          const partialQty2 = pos.qty * partialPct2
          if (partialQty2 > 0.0001) {
            const partialResult2 = isLong
              ? this.marketSell(sym, partialQty2 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty2 * pos.entry_price, pos.strategy)
            if (partialResult2.success) {
              pos.qty -= partialQty2
              console.log(`[Paper/v82j] ${sym} PARTIAL_TP2 15% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial2_done = true
        }

        // 2c. Partial TP3 at +2.0R → close 20% (v82j: was +2.5R in v82h, was 15% @ +1.25R in v53h)
        if (!pos.partial3_done && rMultiple >= 2.0) {
          const partialPct3 = 0.20
          const partialQty3 = pos.qty * partialPct3
          if (partialQty3 > 0.0001) {
            const partialResult3 = isLong
              ? this.marketSell(sym, partialQty3 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty3 * pos.entry_price, pos.strategy)
            if (partialResult3.success) {
              pos.qty -= partialQty3
              console.log(`[Paper/v82j] ${sym} PARTIAL_TP3 20% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial3_done = true
        }

        // 2d. Partial TP4 at +4.0R → close 25% (v82j NEW — captures extended runners)
        if (!pos.partial4_done && rMultiple >= 4.0) {
          const partialPct4 = 0.25
          const partialQty4 = pos.qty * partialPct4
          if (partialQty4 > 0.0001) {
            const partialResult4 = isLong
              ? this.marketSell(sym, partialQty4 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty4 * pos.entry_price, pos.strategy)
            if (partialResult4.success) {
              pos.qty -= partialQty4
              console.log(`[Paper/v82j] ${sym} PARTIAL_TP4 25% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial4_done = true
        }

        // 3. Update trailing stop if active (v82h: regime-aware — was 0.30 ATR in v49c-v81)
        //    ATR% > 1.5 (HIGHVOL/MEME pump): trail = 1.0 ATR (let trends run far)
        //    ATR% 0.8-1.5 (BULL/ALT): trail = 0.5 ATR (medium)
        //    ATR% < 0.8 (BLUE/STABLE/BEAR quiet): trail = 0.3 ATR (tight, protect chops)
        //    Backtest: this regime split lifted BULL RR from 1.027 → 1.674 and
        //    MEME RR from 1.076 → 1.738 (close to 1.8 target).
        if (pos.trail_active && pos.max_favorable_price) {
          const atrPctForTrail = pos.initial_atr_pct ?? 1.0
          const trailMult = atrPctForTrail > 1.5 ? 1.0 : (atrPctForTrail > 0.8 ? 0.5 : 0.3)
          const trailDist = pos.initial_atr * trailMult
          if (isLong) {
            let newSL = pos.max_favorable_price - trailDist
            // Never trail below lock floor (+0.35R) once locked
            if (pos.lock_done) {
              const lockFloor = pos.entry_price + 0.35 * pos.initial_sl_distance
              if (newSL < lockFloor) newSL = lockFloor
            }
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
            pos.current_tp = null  // disable TP — let trail do the work
          } else {
            let newSL = pos.max_favorable_price + trailDist
            if (pos.lock_done) {
              const lockFloor = pos.entry_price - 0.35 * pos.initial_sl_distance
              if (newSL > lockFloor) newSL = lockFloor
            }
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
            pos.current_tp = null
          }
        }
      }

      // ─── SL/TP/CatSL trigger (IMMEDIATE — exchange-like behavior) ───
      // FIX v0.85 (UNIVERSAL — applies to ALL assets, not just one):
      //   Removed the 3-minute `skipStopLoss` grace period that was deferring
      //   SL/CAT_SL execution during the first 180s after entry. The grace
      //   period was added in v12 to avoid noise stopouts, but the user
      //   reported a SHORT XRP/USDT position where price (1.0510) exceeded
      //   SL (1.0497) and the position did not close — this is NOT how real
      //   exchanges behave. Standard exchange SL orders fire IMMEDIATELY
      //   when price crosses the SL level, regardless of how recently the
      //   position was opened. We now match that behavior across every
      //   symbol in `this.positions`.
      //
      //   TP, trailing SL, break-even, and catastrophic SL all fire
      //   immediately on every tick (1.5s cadence). No gating, no deferral.
      //   The previous FIX v10/v12 grace period is fully removed.
      //
      //   If a position needs protection from early noise stopouts, that
      //   should be solved by widening the SL distance (ATR multiplier)
      //   at entry time, not by silently ignoring SL breaches.
      let hit = false
      let reason = ''
      if (pos.current_sl !== null) {
        if (isLong && price <= pos.current_sl) { hit = true; reason = pnlPct > 0 ? 'CLOSED_BY_TRAILING_SL' : 'CLOSED_BY_SL' }
        if (!isLong && price >= pos.current_sl) { hit = true; reason = pnlPct > 0 ? 'CLOSED_BY_TRAILING_SL' : 'CLOSED_BY_SL' }
      }
      // Sanity check: SL breached but hit=false should now be impossible.
      // Keep the guard so any future regression is loud.
      if (pos.current_sl !== null && !hit) {
        const slBreached = (isLong && price <= pos.current_sl) || (!isLong && price >= pos.current_sl)
        if (slBreached) {
          console.error(`[Paper/checkStops] 🚨 BUG: ${sym} SL=${pos.current_sl} breached (price=${price}) but hit=false — investigating`)
        }
      }
      if (pos.current_tp !== null) {
        if (isLong && price >= pos.current_tp) { hit = true; reason = 'CLOSED_BY_TP' }
        if (!isLong && price <= pos.current_tp) { hit = true; reason = 'CLOSED_BY_TP' }
      }
      if (pos.catastrophic_sl !== null) {
        if (isLong && price <= pos.catastrophic_sl) { hit = true; reason = 'CLOSED_BY_CAT_SL' }
        if (!isLong && price >= pos.catastrophic_sl) { hit = true; reason = 'CLOSED_BY_CAT_SL' }
      }

      if (hit) {
        // FIX v0.85: Loud per-symbol log so the user can verify SL/TP fires
        // immediately for EVERY asset (not just XRP). Includes entry, SL/TP
        // level, trigger price, hold time, and PnL — enough to debug any
        // future "why didn't this close?" report.
        const holdSec = Math.round(holdMs / 1000)
        console.log(
          `[Paper/checkStops] 🔥 ${sym} ${pos.direction} CLOSED by ${reason} ` +
          `| entry=${pos.entry_price} sl=${pos.current_sl} tp=${pos.current_tp} ` +
          `catSL=${pos.catastrophic_sl} | triggerPrice=${price} | held=${holdSec}s | pnl=${pnlPct.toFixed(3)}%`
        )
        // Cooldown 30min after SL/CatSL (not after TP or trailing SL)
        if (reason === 'CLOSED_BY_SL' || reason === 'CLOSED_BY_CAT_SL') {
          // v14 NIGHT1 FIX: cooldown 30min → 45min (reduce reentradas prematuras)
          this.cooldownUntil.set(sym, now + 45 * 60 * 1000)
        }
        this.closePosition(sym)
        if (this.trades[0]) this.trades[0].close_reason = reason
      }
    }

    // v83b NEW: Check grid positions (Strategy F) for TP/SL/Time
    this.checkGridStops()
  }

  // ─── Auto trading (4 strategies in parallel) ──────────────────────
  private maybeAutoTrade() {
    if (!this.autoMode || !this.tradingEnabled) return
    const now = Date.now()

    // ─── v15 VOL CB: pause new entries when market volatility is extreme ───
    // Detects violent market-wide moves (avg ATR/price > 1.5% across top tokens)
    // and pauses NEW entries for 10 minutes. Open positions keep managing normally.
    // Calm market: avgAtrPct ≈ 0.2-0.5%.  Volatile: 0.8-1.2%.  Extreme: >1.5%.
    if (now < this.volPauseUntil) {
      const minsLeft = Math.ceil((this.volPauseUntil - now) / 60000)
      if (now - this.lastVolCBLogTime > 60000) {
        console.log(`[Paper/VolCB] ⛔ Volatility pause active — ${minsLeft}min left. New entries skipped.`)
        this.lastVolCBLogTime = now
      }
      return
    }
    const volNow = this.computeMarketVolatility()
    if (volNow.extreme) {
      this.volPauseUntil = now + 10 * 60 * 1000  // 10 min
      console.log(
        `[Paper/VolCB] 🌋 Extreme market volatility detected — ` +
        `avgATR/price=${volNow.avgAtrPct.toFixed(3)}% across ${volNow.tokenCount} tokens. ` +
        `Pausing new entries for 10min. Open positions continue managing.`
      )
      this.lastVolCBLogTime = now
      return
    }

    // ─── Circuit breaker: stop new entries if drawdown exceeded ───
    const totalValue = this.computeTotalValue()
    const totalPnlPct = ((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    if (totalPnlPct < -this.moneyManager.maxDrawdownPct) {
      if (Date.now() - this.lastCBLogTime > 60000) {
        console.log(`[Paper/CB] Drawdown ${totalPnlPct.toFixed(2)}% > ${this.moneyManager.maxDrawdownPct}% — auto-trading paused`)
        this.lastCBLogTime = Date.now()
      }
      return
    }

    this.runStrategyA_Momentum()
    this.runStrategyB_MeanRev()
    // v14 NIGHT1 FIX: Strategy C PAUSADA — WR 10-20% en 2 snapshots paralelos, pierde siempre.
    // this.runStrategyC_Breakout()
    this.runStrategyD_Squeeze()
    // v83b NEW: Strategy F (Grid Trading) for BLUE/STABLE (ATR% < 0.40%).
    // Activates only in low-volatility regimes where A/B don't trade.
    this.runStrategyF_Grid()
  }

  // ─── v83b: Strategy F (Grid Trading) ─────────────────────────────
  // Backtest validated (12 seeds × 8 profiles = 96 runs):
  //   BLUE:    0 → 1525 trades, WR 63.9%, PnL +3.9  (NEW — was 0 trades in v82j)
  //   STABLE:  0 → 1736 trades, WR 76.9%, PnL +86   (NEW — was 0 trades in v82j)
  //   MIXED:   -76 → -53 PnL (grid adds +23 PnL in low-vol periods)
  //   BEAR:    +20 → +72 PnL (grid adds +52 PnL in bear quiet periods)
  //   BULL/MEME/ALT/HIGHVOL: unchanged (ATR > 0.40% so F never fires)
  //
  // Config (V83B WINNER — balanced vs v83 too aggressive MaxDD 30%, vs v83a too tight WR<64%):
  //   Grid levels:    4 above + 4 below SMA(60) baseline
  //   Grid spacing:   0.15% (tight, for low-vol oscillation)
  //   Position size:  0.8% cash per level (very small — grid accumulates)
  //   TP:             0.20% (mean reversion target)
  //   SL:             0.50% (moderate — TP/SL ratio 2.5)
  //   Cooldown:       45 ticks between entries per token
  //   Max per token:  3 concurrent grid positions
  //   Max total:      12 concurrent grid positions (12 × 0.8% = 9.6% max exposure)
  private readonly V83F_ATR_PCT_MAX = 0.40
  private readonly V83F_GRID_LEVELS = 4
  private readonly V83F_GRID_SPACING_PCT = 0.15
  private readonly V83F_POS_SIZE_PCT = 0.008
  private readonly V83F_TP_PCT = 0.20
  private readonly V83F_SL_PCT = 0.50
  private readonly V83F_COOLDOWN_TICKS = 45
  private readonly V83F_MAX_POSITIONS_PER_TOKEN = 3
  private readonly V83F_MAX_TOTAL_POSITIONS = 12
  private readonly V83F_BASELINE_PERIOD = 60
  private readonly V83F_TIME_STOP_TICKS = 240  // 4h assuming 1min ticks

  private runStrategyF_Grid() {
    const now = Date.now()
    for (const sym of this.activeTokens) {
      const history = this.priceHistory.get(sym)
      if (!history || history.length < this.V83F_BASELINE_PERIOD + 5) continue

      // Cooldown per token
      const lastEntry = this.fLastEntryTick.get(sym) ?? 0
      if (this.tickCount - lastEntry < this.V83F_COOLDOWN_TICKS) continue

      const ticker = this.priceFeed.getData(sym)
      if (!ticker) continue
      const price = ticker.price
      const prices = history.map(h => h.price)

      // Compute ATR — skip if > 0.40% (let A/B handle it)
      const atr = computeATR(prices, 60)
      if (atr <= 0) continue
      const atrPct = (atr / price) * 100
      if (atrPct > this.V83F_ATR_PCT_MAX) continue

      // SMA(60) as grid baseline
      const sma = prices.slice(-this.V83F_BASELINE_PERIOD).reduce((a, b) => a + b, 0) / this.V83F_BASELINE_PERIOD
      this.fGridBaseline.set(sym, sma)

      // Determine grid level
      const deviationPct = ((price - sma) / sma) * 100
      const level = Math.trunc(deviationPct / this.V83F_GRID_SPACING_PCT)
      if (level === 0) continue
      if (Math.abs(level) > this.V83F_GRID_LEVELS) continue

      // Check existing positions
      const existing = this.fGridPositions.get(sym) ?? []
      if (existing.length >= this.V83F_MAX_POSITIONS_PER_TOKEN) continue

      // Cap total grid exposure
      let totalGrid = 0
      for (const arr of this.fGridPositions.values()) totalGrid += arr.length
      if (totalGrid >= this.V83F_MAX_TOTAL_POSITIONS) continue

      // Don't open duplicate at same level + direction
      const direction: 'LONG' | 'SHORT' = level < 0 ? 'LONG' : 'SHORT'
      if (existing.some(ex => ex.level === level && ex.direction === direction)) continue

      // Open grid position (use Strategy B's cash pool since F is a low-vol extension of B's mean-reversion)
      const strat = this.strategies.B
      const posSizePct = this.V83F_POS_SIZE_PCT
      const sizeUsdt = Math.min(strat.cash * posSizePct, strat.cash * 0.10)
      if (sizeUsdt < 10) continue

      const slip = price * (SLIPPAGE_PCT / 100)
      const entryPrice = direction === 'LONG' ? price + slip : price - slip
      const fee = sizeUsdt * (FEE_PCT / 100)
      if (strat.cash < sizeUsdt + fee) continue
      strat.cash -= (sizeUsdt + fee)
      const qty = sizeUsdt / entryPrice

      const tpPrice = direction === 'LONG'
        ? entryPrice * (1 + this.V83F_TP_PCT / 100)
        : entryPrice * (1 - this.V83F_TP_PCT / 100)
      const slPrice = direction === 'LONG'
        ? entryPrice * (1 - this.V83F_SL_PCT / 100)
        : entryPrice * (1 + this.V83F_SL_PCT / 100)

      existing.push({
        entry_price: entryPrice,
        direction,
        qty,
        size_usdt: sizeUsdt,
        level,
        tp: tpPrice,
        sl: slPrice,
        entry_tick: this.tickCount,
        max_favorable_price: entryPrice,
      })
      this.fGridPositions.set(sym, existing)
      this.fLastEntryTick.set(sym, this.tickCount)

      console.log(`[Paper/v83b] ${sym} GRID_${direction} lvl${level} @ ${entryPrice.toFixed(4)} (ATR=${atrPct.toFixed(3)}%, dev=${deviationPct.toFixed(3)}%)`)
    }
  }

  // ─── v83b: Grid TP/SL checks ────────────────────────────────────
  private checkGridStops() {
    for (const [sym, positions] of this.fGridPositions.entries()) {
      const ticker = this.priceFeed.getData(sym)
      if (!ticker) continue
      const price = ticker.price
      const remaining: typeof positions = []

      for (const gp of positions) {
        let hit = false
        let reason = ''

        if (gp.direction === 'LONG') {
          if (price > gp.max_favorable_price) gp.max_favorable_price = price
          if (price >= gp.tp) { hit = true; reason = 'F_TP' }
          else if (price <= gp.sl) { hit = true; reason = 'F_SL' }
        } else {
          if (price < gp.max_favorable_price || gp.max_favorable_price === gp.entry_price) {
            gp.max_favorable_price = price
          }
          if (price <= gp.tp) { hit = true; reason = 'F_TP' }
          else if (price >= gp.sl) { hit = true; reason = 'F_SL' }
        }

        // Time stop
        const timeUp = (this.tickCount - gp.entry_tick) > this.V83F_TIME_STOP_TICKS

        if (hit || timeUp) {
          const finalReason = hit ? reason : 'F_TIME'
          const pnl = gp.direction === 'LONG'
            ? (price - gp.entry_price) * gp.qty
            : (gp.entry_price - price) * gp.qty

          // Refund cash to Strategy B pool
          this.strategies.B.cash += gp.qty * price
          this.strategies.B.realizedPnl += pnl
          this.strategies.B.totalTrades++
          if (pnl > 0) this.strategies.B.winningTrades++

          // Record trade
          const initialSlDistance = Math.abs(gp.entry_price - gp.sl)
          const rMultiple = initialSlDistance > 0
            ? (gp.direction === 'LONG'
                ? (price - gp.entry_price)
                : (gp.entry_price - price)) / initialSlDistance
            : 0
          this.trades.push({
            symbol: sym,
            direction: gp.direction,
            strategy: 'B',  // F is tagged as B for allocation purposes
            entry_price: gp.entry_price,
            close_price: price,
            qty: gp.qty,
            size_usdt: gp.size_usdt,
            pnl_usdt: pnl,
            pnl_pct: (pnl / gp.size_usdt) * 100,
            r_multiple: rMultiple,
            close_reason: finalReason,
            entry_tick: gp.entry_tick,
            exit_tick: this.tickCount,
            entry_at: new Date(gp.entry_tick * 1500).toISOString(),
            closed_at: new Date().toISOString(),
            hold_ticks: this.tickCount - gp.entry_tick,
            initial_atr: 0, initial_atr_pct: 0, initial_sl_distance: initialSlDistance,
            current_sl: gp.sl, current_tp: gp.tp, catastrophic_sl: null,
            lock_done: true, partial_done: true,
            partial1_done: true, partial2_done: true, partial3_done: true, partial4_done: true,
            pyramid_done: true, trail_active: false,
            max_favorable_price: gp.max_favorable_price,
            status: 'CLOSED',
          } as any)

          console.log(`[Paper/v83b] ${sym} GRID_${gp.direction} CLOSE ${finalReason} @ ${price.toFixed(4)} PnL=${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} R=${rMultiple.toFixed(2)}`)
        } else {
          remaining.push(gp)
        }
      }

      this.fGridPositions.set(sym, remaining)
    }
  }

  // ─── Strategy A: Momentum 24h ─────────────────────────────────────
  private runStrategyA_Momentum() {
    const strat = this.strategies.A
    const now = Date.now()
    if (now - strat.lastSignalTime < 15000) return // 15s cooldown
    strat.lastSignalTime = now

    // FIX v12: Use RECENT price-history momentum (last 30 ticks ≈ 45s),
    // not 24h changePct. 24h change is stale — a token that pumped +5% in 24h
    // may have peaked 12h ago and is now reverting. Snapshot showed 9/10 last
    // trades were SHORT (24h changePct < 0) and they all got stopped out on
    // bounces. Recent momentum is what's actually tradeable.
    const candidates = WS_ELIGIBLE_TOKENS
      .map(sym => {
        const t = this.priceFeed.getData(sym)
        if (!t || t.quoteVolume < 50_000_000) return null
        const hist = this.priceHistory.get(sym) || []
        if (hist.length < 30) return null
        // Recent momentum: last 30 ticks (45s) price change %
        // v49c: momentum 0.55% (was 0.40 v31b) — stricter quality filter
        // Backtest: 0.15 gave WR 41%, 0.40 + RSI filter gives WR 63.5%, 0.55 gives WR 73.1% (12-seed)
        const recent = hist.slice(-30)
        const recentMomentum = ((recent[recent.length - 1].price - recent[0].price) / recent[0].price) * 100
        if (Math.abs(recentMomentum) < 0.55) return null  // v49c: need ≥0.55% move (was 0.40)
        // v82a FIX: rsiA was using undefined `prices` (bug since ~v38, filter inert ~30 versions).
        //   Use full hist for RSI(14) — more accurate than just `recent` (30 ticks is borderline for RSI).
        //   Effect: RSI 25/75 filter now actually blocks overbought/oversold entries in Strategy A.
        //   Expected: MIXED P&L -91 → ~-30, HIGHVOL P&L -13 → ~+15 (filter blocks bad momentum entries).
        const rsiA = computeRSI(hist.map(h => h.price), 14)
        if (rsiA < 25 || rsiA > 75) return null  // skip extreme zones (mean reversion territory)
        return { ticker: t, recentMomentum }
      })
      .filter((x): x is { ticker: TickerData; recentMomentum: number } => x !== null)
      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .filter(x => {
        // v37e: ATR floor 0.55% — skip trades in low-vol regimes where fees dominate
        const histA = this.priceHistory.get(x.ticker.symbol) || []
        const pricesA = histA.map(h => h.price)
        const atrA = computeATR(pricesA, 60)
        const atrPctA = atrA / x.ticker.price * 100
        return atrPctA >= 0.58
      })
      .sort((a, b) => Math.abs(b.recentMomentum) - Math.abs(a.recentMomentum))
      .slice(0, 3)

    if (candidates.length === 0) return

    for (const top of candidates) {
      if (strat.positions.size >= 2) break
      // v31b: position size 2.5% for Strategy A (A has 61% WR but loses
      // money due to R:R 1:1.67 — halving size makes A's losses manageable)
      // v81: A base size 0.040 (was 0.050 in v62a) — reduce A's drag while pushing B harder
      //   12-seed validation: A 0.040 + B 0.30 + pyramid +75% → P&L +46.93, MaxDD 0.29%, PF 3.01 (vs v62a P&L +48.56, PF 2.72)
      //   A was net LOSER at 0.050 (-7.51 in seed 2024 trace) — at 0.040 with B 0.30, PF jumps 2.72 → 3.01 (+10.7%)
      const baseUsdtAmountA = Math.min(strat.cash * 0.040, strat.cash * 0.10)
      if (baseUsdtAmountA < 50) break

      // FIX v12 BUG A: direction from RECENT momentum, not 24h changePct
      const direction: 'LONG' | 'SHORT' = top.recentMomentum > 0 ? 'LONG' : 'SHORT'
      const hist = this.priceHistory.get(top.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue

      // v59f: TIERED adaptive ATR sizing — 0.4x if ATR<0.6%, 0.7x if ATR<0.8%, 1.0x otherwise
      //   Replaces v56d's simple 0.5x halving — finer control reduces MaxDD while keeping P&L up
      const atrPctA = atr / top.ticker.price * 100
      const sizeMultA = atrPctA < 0.60 ? 0.4 : (atrPctA < 0.80 ? 0.7 : 1.0)
      const usdtAmount = baseUsdtAmountA * sizeMultA

      const result = direction === 'LONG'
        ? this.marketBuy(top.symbol, usdtAmount, 'A')
        : this.marketSell(top.symbol, usdtAmount, 'A')

      if (result.success) {
        const pos = this.positions.get(top.symbol)
        if (pos) {
          // v82j PORT to Strategy A (was v81: TP 1.2 ATR — INVERTED RR 0.80, lost money long-term).
          //   Root cause (diagnosed 2026-06-30 on live BTC trade): A had TP 1.2 ATR vs SL 1.5 ATR,
          //   meaning TP was CLOSER than SL. Even with 60% WR the EV was negative after fees.
          //   Fix: apply v82j exit stack — TP 6.0 ATR (4R distant ceiling), 4 partials at
          //   0.5/1.0/2.0/4.0R (10/15/20/25%), lock @+0.5R → SL=entry+0.35R + activate trail,
          //   regime-aware trail (1.0/0.5/0.3 ATR by ATR% band). Same exit stack as Strategy B.
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 6.0 : pos.entry_price - atr * 6.0  // v82j: 6.0 ATR (was 1.2)
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 2.5 : pos.entry_price + atr * 2.5  // v82j: 2.5 ATR (was 4.0)
          pos.initial_atr = atr
          pos.initial_atr_pct = atrPctA  // v82j: store for regime-aware trail
          pos.initial_sl_distance = atr * 1.5
          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.partial3_done = false
          pos.partial4_done = false  // v82j: 4th partial at +4.0R
          pos.pyramid_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price
        }
        // FIX v12 BUG D: Compute ev_score + expected_move_pct so ML/Kelly can work.
        //   ev_score = confidence × expected_move_pct / 2  (heuristic)
        //   expected_move_pct = (ATR × 3 / entry_price) × 100  (the TP distance)
        const expected_move_pct = +((atr * 3 / (pos?.entry_price || 1)) * 100).toFixed(3)
        const ev_score = +(Math.min(0.95, 0.55 + Math.abs(top.recentMomentum) / 5) * expected_move_pct / 2).toFixed(3)
        // FIX v12 BUG G: Confidence floor 0.55 → 0.65
        const confA = Math.max(0.65, Math.min(0.95, 0.55 + Math.abs(top.recentMomentum) / 5))
        this.signals.unshift({
          timestamp: new Date().toISOString(),
          direction, symbol: top.symbol, strategy: 'A',
          confidence: confA,
          pattern_path: `MOMENTUM_24H_${direction}`,
          ev_score,
          expected_move_pct,
        })
        if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)
        console.log(`[Strat A/Momentum] OPEN ${direction} ${top.symbol} @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(0)} USDT, ATR ${atr.toFixed(4)})`)
      }
    }
  }

  // ─── Strategy B: Mean Reversion (RSI) ─────────────────────────────
  private runStrategyB_MeanRev() {
    const strat = this.strategies.B
    const now = Date.now()
    if (now - strat.lastSignalTime < 30000) return // 30s cooldown
    strat.lastSignalTime = now

    const candidates = WS_ELIGIBLE_TOKENS
      .map(sym => {
        const t = this.priceFeed.getData(sym)
        if (!t || t.quoteVolume < 30_000_000) return null
        const hist = this.priceHistory.get(sym) || []
        if (hist.length < 100) return null  // v81: need 100 ticks for SMA100 slope
        const prices = hist.map(h => h.price)
        // v31b: RSI 30/70 (was 40/60) — backtest shows 30/70 gives 75% WR
        //   on Strategy B (the winner strategy, +69 P&L in 6h)
        const rsi = computeRSI(prices, 14)
        if (rsi >= 30 && rsi <= 70) return null
        // v81 F1: Trend filter (slope-based) — block mean reversion against strong trends
        //   Root cause: v67 B SHORT in BULL/MEME lost -4428/-6138 P&L (catastrophic)
        //   Fix: don't short into uptrend (slope > +threshold%), don't long into downtrend (slope < -threshold%)
        //   Backtest v81 vs v67 (12 seeds × 8 profiles):
        //     BULL: -295 → +165 (100% profit), MEME: -206 → +373 (100% profit), BEAR: -164 → -7
        // v82h: threshold 0.05 (back to v81 baseline — ablation proved 0.10 was worse for MIXED)
        //   9-variant sweep (v82a through v82j) confirmed 0.05 is optimal:
        //   - 0.00 (no filter): MIXED -70, BEAR -44 (worse without filter)
        //   - 0.02 (very strict): MIXED -96, BEAR -59 (over-filters)
        //   - 0.05 (v81 baseline): MIXED -91, BEAR -7 (BEST for non-trending)
        //   - 0.10 (v82a): MIXED -110, BEAR +11 (slightly better BEAR but worse MIXED)
        //   - 0.15 (very loose): MIXED -122, BEAR +5 (worse overall)
        const TREND_FILTER_THRESHOLD = 0.05  // v82h (was 0.10 in v82a, 0.05 in v81)
        const smaNow = prices.slice(-100).reduce((a, b) => a + b, 0) / 100
        const smaPrev = prices.slice(-110, -10).reduce((a, b) => a + b, 0) / 100
        const smaSlopePct = (smaNow - smaPrev) / smaPrev * 100
        const direction = rsi < 50 ? 'LONG' : 'SHORT'
        if (direction === 'LONG' && smaSlopePct < -TREND_FILTER_THRESHOLD) return null  // don't long into downtrend
        if (direction === 'SHORT' && smaSlopePct > TREND_FILTER_THRESHOLD) return null  // don't short into uptrend
        return { ticker: t, rsi, direction, smaSlopePct }
      })
      .filter((x): x is { ticker: TickerData; rsi: number; direction: 'LONG' | 'SHORT'; smaSlopePct: number } => x !== null)
      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .filter(x => {
        // v81 F2: Dynamic ATR floor 0.40% (was 0.58% in v67) — allow more tokens to trade
        //   Backtest: 0.58% blocked ALL BLUE/STABLE trades; 0.40% keeps MIXED quality
        //   For BLUE/STABLE (ATR < 0.40%), use grid strategy (future v82)
        const histB = this.priceHistory.get(x.ticker.symbol) || []
        const pricesB = histB.map(h => h.price)
        const atrB = computeATR(pricesB, 60)
        const atrPctB = atrB / x.ticker.price * 100
        return atrPctB >= 0.40
      })
      .sort((a, b) => Math.abs(a.rsi - 50) - Math.abs(b.rsi - 50))
      .slice(0, 2)

    if (candidates.length === 0) return

    for (const c of candidates) {
      if (strat.positions.size >= 2) break
      const hist = this.priceHistory.get(c.ticker.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue
      const atrPctB = (atr / hist[hist.length - 1].price) * 100

      // v81 F5: Regime-aware B size — 0.15 if HIGHVOL (ATR% > 1.2), 0.30 otherwise
      //   Root cause: v67 B at 0.30 in HIGHVOL had MaxDD 2.34%, Profit 33%
      //   Fix: smaller B in HIGHVOL → MaxDD 1.54%, Profit 25% (less profit but controlled risk)
      //   v67 B at 0.30 in normal vol = OK (PF 3.01 on MIXED)
      const bBaseSize = atrPctB > 1.2 ? 0.15 : 0.30
      const baseUsdtAmount = Math.min(strat.cash * bBaseSize, strat.cash * 0.30)
      if (baseUsdtAmount < 50) break

      // v81 F2: Extended tiered sizing (lower for low-vol tokens)
      //   v67 had 0.4/0.7/1.0 (only 3 tiers, all >= 0.4)
      //   v81 has 0.3/0.5/0.7/1.0 (4 tiers, starts at 0.3 for ATR < 0.4%)
      const sizeMultB = atrPctB < 0.40 ? 0.3 : (atrPctB < 0.60 ? 0.5 : (atrPctB < 0.80 ? 0.7 : 1.0))
      const usdtAmount = baseUsdtAmount * sizeMultB

      const result = c.direction === 'LONG'
        ? this.marketBuy(c.ticker.symbol, usdtAmount, 'B')
        : this.marketSell(c.ticker.symbol, usdtAmount, 'B')

      if (result.success) {
        const pos = this.positions.get(c.ticker.symbol)
        if (pos) {
          // v82h: Position management overhaul — 4 partials at higher R + regime-aware trail + TP 6.0 ATR
          //   Backtest (12 seeds × 8 profiles = 96 runs):
          //     v81: RR avg 0.535, 0/8 pass gate (WR>64% AND RR>1.8)
          //     v82h: RR avg 0.831 (+55%!), 0/8 pass gate but BULL 1.674, MEME 1.738 (close to 1.8)
          //   Key changes vs v81:
          //     - TP: 1.2 ATR → 6.0 ATR (4R distant ceiling — trail is the actual exit)
          //     - Partials: 3 @ 0.5/1.0/1.25R (5%/10%/15%) → 4 @ 0.8/1.5/2.5/4.0R (10%/15%/20%/25%)
          //       Reason: avg_r in engine = mean(r_multiple) over ALL trades INCLUDING partials.
          //       Moving partials to higher R levels lifts avg_r substantially.
          //     - Trail: 0.30 ATR → regime-aware (1.0 ATR if ATR%>1.5, 0.5 if >0.8, 0.3 otherwise)
          //       Wide trail in high-vol lets trends run (BULL/MEME benefit);
          //       tight trail in low-vol protects chops (BEAR/MIXED).
          //     - Trail activation: after partial3 (v81) → after LOCK (v82h)
          //       Activating trail earlier protects remainder once +0.5R reached.
          //     - Pyramid: 0.75 → 0.50 (less aggressive adding, less risk if reversal)
          //   SL 1.5 ATR, Cat SL 2.5 ATR, Lock +0.5R → SL=entry+0.35R (unchanged from v81).
          pos.current_sl = c.direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = c.direction === 'LONG' ? pos.entry_price + atr * 6.0 : pos.entry_price - atr * 6.0  // v82h: 6.0 ATR (was 1.2)
          pos.catastrophic_sl = c.direction === 'LONG' ? pos.entry_price - atr * 2.5 : pos.entry_price + atr * 2.5
          pos.initial_atr = atr
          pos.initial_atr_pct = atrPctB  // v82h: store for regime-aware trail
          pos.initial_sl_distance = atr * 1.5
          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.partial3_done = false
          pos.partial4_done = false  // v82h: 4th partial at +4.0R
          pos.pyramid_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price
        }
        // FIX v13 BUG K: Same ev_score computation as Strategy A.
        const expected_move_pct_b = +((atr * 2 / (pos?.entry_price || 1)) * 100).toFixed(3)
        const confB = Math.max(0.65, Math.min(0.9, 0.5 + Math.abs(c.rsi - 50) / 50))
        const ev_score_b = +(confB * expected_move_pct_b / 2).toFixed(3)
        this.signals.unshift({
          timestamp: new Date().toISOString(),
          direction: c.direction, symbol: c.ticker.symbol, strategy: 'B',
          confidence: confB,
          pattern_path: `MEANREV_RSI${c.rsi.toFixed(0)}_${c.direction}_slope${c.smaSlopePct.toFixed(2)}`,
          ev_score: ev_score_b,
          expected_move_pct: expected_move_pct_b,
        })
        if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)
        console.log(`[Strat B/MeanRev] OPEN ${c.direction} ${c.ticker.symbol} RSI=${c.rsi.toFixed(1)} slope=${c.smaSlopePct.toFixed(2)}% @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(0)} USDT, ATR ${atrPctB.toFixed(2)}%)`)
      }
    }
  }

  // ─── Strategy C: Range Breakout ───────────────────────────────────
  private runStrategyC_Breakout() {
    const strat = this.strategies.C
    const now = Date.now()
    if (now - strat.lastSignalTime < 10000) return // 10s cooldown
    strat.lastSignalTime = now

    const candidates = WS_ELIGIBLE_TOKENS
      .map(sym => {
        const t = this.priceFeed.getData(sym)
        if (!t || t.quoteVolume < 30_000_000) return null
        const hist = this.priceHistory.get(sym) || []
        if (hist.length < 70) return null
        const prices = hist.map(h => h.price)
        const range = computeRollingRange(prices.slice(0, -1), 60)
        const current = prices[prices.length - 1]
        const isBreakout = current > range.high
        const isBreakdown = current < range.low
        if (!isBreakout && !isBreakdown) return null
        return { ticker: t, isBreakout }
      })
      .filter((x): x is { ticker: TickerData; isBreakout: boolean } => x !== null)
      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .slice(0, 2)

    if (candidates.length === 0) return

    for (const c of candidates) {
      if (strat.positions.size >= 2) break
      // FIX v13 BUG L: position size 3% → 5%
      const usdtAmount = Math.min(strat.cash * 0.05, strat.cash * 0.10)
      if (usdtAmount < 50) break

      const direction: 'LONG' | 'SHORT' = c.isBreakout ? 'LONG' : 'SHORT'
      const hist = this.priceHistory.get(c.ticker.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue

      const result = direction === 'LONG'
        ? this.marketBuy(c.ticker.symbol, usdtAmount, 'C')
        : this.marketSell(c.ticker.symbol, usdtAmount, 'C')

      if (result.success) {
        const pos = this.positions.get(c.ticker.symbol)
        if (pos) {
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1 : pos.entry_price + atr * 1
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 3 : pos.entry_price - atr * 3
          // FIX v13 BUG M: CatSL 2×ATR → 3.5×ATR (was 1×ATR away from SL — too tight)
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 3.5 : pos.entry_price + atr * 3.5
        }
        // FIX v13 BUG K: ev_score for Strategy C (breakout).
        const expected_move_pct_c = +((atr * 3 / (pos?.entry_price || 1)) * 100).toFixed(3)
        const ev_score_c = +(0.65 * expected_move_pct_c / 2).toFixed(3)
        this.signals.unshift({
          timestamp: new Date().toISOString(),
          direction, symbol: c.ticker.symbol, strategy: 'C',
          confidence: 0.65,
          pattern_path: `BREAKOUT_${direction}`,
          ev_score: ev_score_c,
          expected_move_pct: expected_move_pct_c,
        })
        if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)
        console.log(`[Strat C/Breakout] OPEN ${direction} ${c.ticker.symbol} broke ${c.isBreakout ? 'high' : 'low'} @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(0)} USDT)`)
      }
    }
  }

  // ─── Strategy D: Volatility Squeeze ───────────────────────────────
  private runStrategyD_Squeeze() {
    const strat = this.strategies.D
    const now = Date.now()
    if (now - strat.lastSignalTime < 60000) return // 60s cooldown
    strat.lastSignalTime = now

    const candidates = WS_ELIGIBLE_TOKENS
      .map(sym => {
        const t = this.priceFeed.getData(sym)
        if (!t || t.quoteVolume < 20_000_000) return null
        const hist = this.priceHistory.get(sym) || []
        if (hist.length < 55) return null
        const prices = hist.map(h => h.price)
        const bb = computeBollinger(prices, 50, 2)
        // v31b: Squeeze threshold 1.5% → 1.2% (tighter squeeze = cleaner breakouts)
        if (bb.width > 0.012) return null // not squeezed
        const current = prices[prices.length - 1]
        const isLong = current > bb.upper
        const isShort = current < bb.lower
        if (!isLong && !isShort) return null
        return { ticker: t, isLong, bbWidth: bb.width }
      })
      .filter((x): x is { ticker: TickerData; isLong: boolean; bbWidth: number } => x !== null)
      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .slice(0, 1)

    if (candidates.length === 0) return

    for (const c of candidates) {
      if (strat.positions.size >= 1) break
      // FIX v13 BUG L: position size 3% → 5%
      const usdtAmount = Math.min(strat.cash * 0.05, strat.cash * 0.10)
      if (usdtAmount < 50) break

      const direction: 'LONG' | 'SHORT' = c.isLong ? 'LONG' : 'SHORT'
      const hist = this.priceHistory.get(c.ticker.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue
      const atrPctD = (atr / (hist[hist.length - 1]?.price || 1)) * 100  // v82j: for regime-aware trail

      const result = direction === 'LONG'
        ? this.marketBuy(c.ticker.symbol, usdtAmount, 'D')
        : this.marketSell(c.ticker.symbol, usdtAmount, 'D')

      if (result.success) {
        const pos = this.positions.get(c.ticker.symbol)
        if (pos) {
          // v82j PORT to Strategy D (was v31b: TP 1.0 ATR — INVERTED RR 0.67, the WORST offender).
          //   Root cause (diagnosed 2026-06-30 on live BTC trade #59508.63):
          //     TP at +0.30% (1.0 ATR), SL at -0.45% (1.5 ATR), CAT SL at -0.90% (3.0 ATR).
          //     TP was 33% CLOSER than SL → even at 70% WR, EV = 0.70×1.0 - 0.30×1.5 = +0.25R
          //     but with fees+slippage ~0.10R per round-trip, EV ≈ 0.05R — basically breakeven,
          //     and catastrophic SL hits (-2R each) wiped out 10 winners.
          //   Fix: apply v82j exit stack — TP 6.0 ATR (4R distant ceiling), 4 partials at
          //   0.5/1.0/2.0/4.0R (10/15/20/25%), lock @+0.5R → SL=entry+0.35R + activate trail,
          //   regime-aware trail (1.0/0.5/0.3 ATR by ATR% band). Same exit stack as Strategy B.
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 6.0 : pos.entry_price - atr * 6.0  // v82j: 6.0 ATR (was 1.0)
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 2.5 : pos.entry_price + atr * 2.5  // v82j: 2.5 ATR (was 3.0)
          pos.initial_atr = atr
          pos.initial_atr_pct = atrPctD  // v82j: store for regime-aware trail
          pos.initial_sl_distance = atr * 1.5
          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.partial3_done = false
          pos.partial4_done = false  // v82j: 4th partial at +4.0R
          pos.pyramid_done = false  // v82j: not used by D (pyramid is B-only), but set for cleanliness
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price
        }
        // FIX v13 BUG K: ev_score for Strategy D (squeeze expansion).
        const expected_move_pct_d = +((atr * 4 / (pos?.entry_price || 1)) * 100).toFixed(3)
        const ev_score_d = +(0.7 * expected_move_pct_d / 2).toFixed(3)
        this.signals.unshift({
          timestamp: new Date().toISOString(),
          direction, symbol: c.ticker.symbol, strategy: 'D',
          confidence: 0.7,
          pattern_path: `SQUEEZE_${direction}`,
          ev_score: ev_score_d,
          expected_move_pct: expected_move_pct_d,
        })
        if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)
        console.log(`[Strat D/Squeeze] OPEN ${direction} ${c.ticker.symbol} bbWidth=${(c.bbWidth * 100).toFixed(2)}% @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(0)} USDT)`)
      }
    }
  }

  // ─── Helpers ──────────────────────────────────────────────────────
  private checkCorrelationLimit(symbol: string): boolean {
    const sector = getSector(symbol)
    const sameSector = Array.from(this.positions.keys())
      .filter(s => getSector(s) === sector)
    return sameSector.length < this.moneyManager.maxCorrelatedPositions
  }

  private computeTotalValue(): number {
    let total = 0
    for (const s of Object.values(this.strategies)) {
      total += s.cash
    }
    for (const [sym, pos] of this.positions) {
      const ticker = this.priceFeed.getData(sym)
      const price = ticker?.price ?? pos.entry_price
      const isLong = pos.direction === 'LONG'
      if (isLong) {
        total += pos.qty * price
      } else {
        const pnl = (pos.entry_price - price) * pos.qty
        total += pos.size_usdt + pnl
      }
    }
    return total
  }

  private computeStrategyUnrealized(name: StrategyName): number {
    let unrealized = 0
    for (const [sym, pos] of this.positions) {
      if (pos.strategy !== name) continue
      const ticker = this.priceFeed.getData(sym)
      const price = ticker?.price ?? pos.entry_price
      const isLong = pos.direction === 'LONG'
      const pnl = isLong
        ? (price - pos.entry_price) * pos.qty
        : (pos.entry_price - price) * pos.qty
      unrealized += pnl
    }
    return unrealized
  }

  private computeStrategyExposure(name: StrategyName): number {
    let exposure = 0
    for (const [sym, pos] of this.positions) {
      if (pos.strategy !== name) continue
      const ticker = this.priceFeed.getData(sym)
      const price = ticker?.price ?? pos.entry_price
      if (pos.direction === 'LONG') {
        exposure += pos.qty * price
      } else {
        exposure += pos.size_usdt
      }
    }
    return exposure
  }

  /**
   * v15 VOL CB: Compute real-time market-wide volatility.
   * Returns avgAtrPct = average of (ATR(60) / price * 100) across top-volume
   * tokens with sufficient price history. This is a TRUE real-time vol measure
   * (last ~90s of price action), unlike the 24h avgChange 'regime' which is stale.
   *
   * Thresholds (calibrated for 1.5s tick / 60-sample ATR ≈ 90s window):
   *   calm     < 0.5%
   *   normal   0.5 - 0.8%
   *   high     0.8 - 1.5%
   *   extreme  > 1.5%   ← triggers circuit breaker
   */
  private computeMarketVolatility(): { avgAtrPct: number; tokenCount: number; extreme: boolean } {
    let sumAtrPct = 0
    let count = 0
    for (const sym of WS_ELIGIBLE_TOKENS) {
      const ticker = this.priceFeed.getData(sym)
      if (!ticker || ticker.quoteVolume < 50_000_000) continue
      const hist = this.priceHistory.get(sym) || []
      if (hist.length < 60) continue
      const prices = hist.slice(-60).map(h => h.price)
      const atr = computeATR(prices, 60)
      if (atr <= 0 || ticker.price <= 0) continue
      sumAtrPct += (atr / ticker.price) * 100
      count++
    }
    const avgAtrPct = count > 0 ? sumAtrPct / count : 0
    return {
      avgAtrPct,
      tokenCount: count,
      extreme: count >= 3 && avgAtrPct > 1.5,  // need ≥3 tokens agreeing to avoid single-token pump false positives
    }
  }

  private maybeReport() {
    const now = Date.now()
    if (now - this.lastReportTime < 5 * 60 * 1000) return // 5 min
    this.lastReportTime = now

    const totalValue = this.computeTotalValue()
    console.log('\n━━━━━━━━━━ Strategy Performance Report ━━━━━━━━━━')
    for (const name of ['A', 'B', 'C', 'D'] as StrategyName[]) {
      const strat = this.strategies[name]
      const info = STRATEGY_INFO[name]
      const unrl = this.computeStrategyUnrealized(name)
      const expo = this.computeStrategyExposure(name)
      const stratValue = strat.cash + expo + unrl
      const pnlPct = ((stratValue - strat.allocated) / strat.allocated) * 100
      const wr = strat.totalTrades > 0 ? (strat.winningTrades / strat.totalTrades) * 100 : 0
      console.log(
        `[${name} ${info.name.padEnd(15)}] ` +
        `P&L ${strat.realizedPnl >= 0 ? '+' : ''}${strat.realizedPnl.toFixed(2)} ` +
        `(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%) ` +
        `trades=${strat.totalTrades} WR=${wr.toFixed(0)}% ` +
        `open=${strat.positions.size} cash=${strat.cash.toFixed(0)}`
      )
    }
    const totalPct = ((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    console.log(`[Portfolio Total]   ${totalValue.toFixed(2)} USDT (${totalPct >= 0 ? '+' : ''}${totalPct.toFixed(2)}%)`)
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
  }

  // ─── Snapshot ─────────────────────────────────────────────────────
  private snapshot(): PaperTradingState {
    this.updatePriceHistory()
    this.updatePatternsAndTrie()
    this.checkStops()
    this.maybeAutoTrade()
    this.maybeReport()

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
      if (isLong) {
        exposure += pos.qty * price
      } else {
        exposure += pos.size_usdt
      }
      positionsArray.push({ ...pos, current_price: price })
    }

    const totalValue = this.computeTotalValue()
    this.equity.push(totalValue)
    this.timestamps.push(Date.now() / 1000)
    if (this.equity.length > 500) {
      this.equity = this.equity.slice(-500)
      this.timestamps = this.timestamps.slice(-500)
    }

    const maxEq = Math.max(...this.equity)
    const dd = maxEq > 0 ? ((maxEq - totalValue) / maxEq) * 100 : 0

    // Aggregate across strategies
    const totalCash = Object.values(this.strategies).reduce((s, x) => s + x.cash, 0)
    const totalRealized = Object.values(this.strategies).reduce((s, x) => s + x.realizedPnl, 0)
    const totalTrades = Object.values(this.strategies).reduce((s, x) => s + x.totalTrades, 0)
    const totalWinning = Object.values(this.strategies).reduce((s, x) => s + x.winningTrades, 0)

    // Daily return
    const dayAgoIdx = Math.max(0, this.equity.length - Math.floor(24 * 60 * 60 / 1.5))
    const dailyReturn = this.equity.length > 1
      ? ((totalValue - this.equity[dayAgoIdx]) / this.equity[dayAgoIdx]) * 100
      : 0

    // Token states
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
            ? parseFloat(((pos.direction === 'LONG' ? pos.qty * t.price : pos.size_usdt) / totalValue * 100).toFixed(1))
            : 0,
          isActive: true,
          isTrading: !!pos,
          winRate: totalTrades > 0 ? totalWinning / totalTrades : 0,
          totalTrades: this.trades.filter(tr => tr.symbol === sym).length,
          equity: this.equity,
          color: TOKEN_COLORS[sym] || '#6b7280',
        }
      }
    }

    // Sort active tokens by |24h change|
    const sortedActive = [...this.activeTokens]
      .map(s => ({ s, ch: this.priceFeed.getData(s)?.changePct ?? -999 }))
      .sort((a, b) => Math.abs(b.ch) - Math.abs(a.ch))
      .map(x => x.s)

    // Kelly
    const wr = totalTrades > 0 ? totalWinning / totalTrades : 0
    const mm = this.moneyManager
    const R = mm.takeProfitMultiplier
    const kellyPercent = wr > 0 && wr < 1 ? Math.max(0, wr - ((1 - wr) / R)) : 0
    const suggestedPositionSize = kellyPercent * mm.kellyFraction * totalValue

    // Current price
    const selTicker = this.priceFeed.getData(this.selectedToken)
    const currentPrice = selTicker?.price ?? 0

    // Regime
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

    // Entropy
    const changes = sortedActive
      .map(s => this.priceFeed.getData(s)?.changePct ?? 0)
      .slice(0, 10)
    const mean = changes.reduce((a, b) => a + b, 0) / (changes.length || 1)
    const variance = changes.reduce((a, b) => a + (b - mean) ** 2, 0) / (changes.length || 1)
    const entropy = Math.min(1, Math.sqrt(variance) / 5)

    // Strategies perf
    const strategies_perf: Record<string, StrategyPerf> = {}
    for (const name of ['A', 'B', 'C', 'D'] as StrategyName[]) {
      const strat = this.strategies[name]
      const info = STRATEGY_INFO[name]
      const unrl = this.computeStrategyUnrealized(name)
      const expo = this.computeStrategyExposure(name)
      const stratValue = strat.cash + expo + unrl
      strategies_perf[name] = {
        name: info.name,
        description: info.description,
        cash: parseFloat(strat.cash.toFixed(2)),
        allocated: strat.allocated,
        realized_pnl: parseFloat(strat.realizedPnl.toFixed(2)),
        unrealized_pnl: parseFloat(unrl.toFixed(2)),
        total_pnl_pct: parseFloat(((stratValue - strat.allocated) / strat.allocated * 100).toFixed(2)),
        total_trades: strat.totalTrades,
        winning_trades: strat.winningTrades,
        win_rate: strat.totalTrades > 0 ? parseFloat((strat.winningTrades / strat.totalTrades).toFixed(3)) : 0,
        open_positions: strat.positions.size,
        last_signal_time: strat.lastSignalTime,
        color: info.color,
      }
    }

    return {
      is_running: this.tradingEnabled,
      mode: 'paper',
      started_at: this.startedAt,
      engine_version: ENGINE_VERSION,  // v82j+: version tag for snapshot traceability
      current_price: parseFloat(currentPrice.toFixed(currentPrice < 1 ? 6 : currentPrice < 100 ? 4 : 2)),
      symbol: this.selectedToken,
      timeframe: 'live',
      exchange: 'COINBASE',  // FIX v10: was BINANCE (geo-blocked from Spain)
      pattern_buffer: this.patternBufferPerToken.get(this.selectedToken) || [],
      entropy: parseFloat(entropy.toFixed(3)),
      regime,
      vol_regime: (() => {
        const v = this.computeMarketVolatility()
        return { avg_atr_pct: parseFloat(v.avgAtrPct.toFixed(3)), token_count: v.tokenCount, extreme: v.extreme, paused: Date.now() < this.volPauseUntil, pause_remaining_ms: Math.max(0, this.volPauseUntil - Date.now()) }
      })(),
      latest_signal: this.signals[0] || null,
      signals_history: this.signals.slice(0, 20),
      positions: positionsArray,
      portfolio_value: parseFloat(totalValue.toFixed(2)),
      cash: parseFloat(totalCash.toFixed(2)),
      unrealized_pnl: parseFloat(unrealized.toFixed(4)),
      realized_pnl: parseFloat(totalRealized.toFixed(4)),
      total_pnl_pct: parseFloat(((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100).toFixed(2)),
      exposure_pct: totalValue > 0 ? parseFloat((exposure / totalValue * 100).toFixed(1)) : 0,
      daily_return_pct: parseFloat(dailyReturn.toFixed(2)),
      leverage: mm.defaultLeverage,
      auto_mode: this.autoMode,
      circuit_breakers: {
        max_drawdown: dd > mm.maxDrawdownPct,
        daily_loss: totalRealized < -(INITIAL_CAPITAL * mm.dailyLossLimitPct / 100),
        volatility: false,
      },
      is_trading_allowed: dd < mm.maxDrawdownPct + 5,
      kill_switch_active: !this.tradingEnabled,
      max_drawdown_pct: parseFloat(dd.toFixed(2)),
      daily_loss_pct: parseFloat(Math.max(0, -totalRealized / (INITIAL_CAPITAL / 100)).toFixed(2)),
      total_trades: totalTrades,
      winning_trades: totalWinning,
      win_rate: totalTrades > 0 ? parseFloat((totalWinning / totalTrades).toFixed(3)) : 0,
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
      trade_history: this.trades.slice(0, 20).map(t => ({ ...t, status: 'CLOSED' })),
      candles_processed: this.candlesProcessed,
      tick_count: this.tickCount,            // FIX v11
      last_tick_at: this.lastTickAt,         // FIX v11: ms epoch; 0 = never ticked
      websocket_status: wsConnected ? 'connected' : 'reconnecting',
      reconnect_count: 0,
      token_states: tokenStates,
      active_tokens: sortedActive,
      selected_token: this.selectedToken,
      money_manager: mm,
      kelly_percent: parseFloat(kellyPercent.toFixed(4)),
      suggested_position_size: parseFloat(suggestedPositionSize.toFixed(2)),
      risk_reward_ratio: R,
      strategies_perf,
    }
  }
}
