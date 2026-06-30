/**
 * useTradingSocket — Hook for PPMT Trading Terminal.
 *
 * NEW: Uses PaperTradingEngine with LIVE prices from Coinbase WS +
 * CoinGecko/Kraken REST (Spain-friendly — no Binance.com geo-block).
 * The terminal now operates in true paper-trading mode:
 *  - Real-time prices from Coinbase WebSocket (no API key)
 *  - 24h change % and volume from CoinGecko REST (one call for all tokens)
 *  - Manual BUY / SELL / CLOSE on any of 82 supported tokens
 *  - Realistic fees (0.1% taker) and slippage (0.05%)
 *  - Starting capital: 10,000 USDT — can grow or shrink for real
 *
 * If NEXT_PUBLIC_BRIDGE_URL is set, the hook ALSO tries to connect to the
 * Python backend via Socket.io for live PPMT signals. If that connection
 * fails, the paper engine keeps running independently on live prices.
 */
'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
import { useTradingStore } from '@/stores/trading-store'
import { LivePriceFeed } from '@/lib/live-price-feed'
import {
  PaperTradingEngine,
  SUPPORTED_TOKENS,
  INITIAL_CAPITAL,
} from '@/lib/paper-trading-engine'

const BRIDGE_FALLBACK_DELAY = 3000

// ─── Global singleton: prevents StrictMode double-mount from creating
// multiple PaperTradingEngine instances that would each open their own
// WebSocket and stomp on each other's state. React 18 StrictMode in dev
// mounts every component twice; without this guard we'd see 2-8 engines
// running simultaneously, all pushing conflicting state updates.
let GLOBAL_ENGINE: PaperTradingEngine | null = null
let GLOBAL_FEED: LivePriceFeed | null = null
let GLOBAL_LISTENER: ((state: any) => void) | null = null
let GLOBAL_REFCOUNT = 0

/**
 * Returns the singleton PaperTradingEngine instance, or null if no
 * component has mounted yet (the engine is created on first mount
 * of useTradingSocket).
 *
 * Used by /api/engine-state to expose the engine snapshot via HTTP
 * for remote debugging.
 */
export function getGlobalEngine(): PaperTradingEngine | null {
  return GLOBAL_ENGINE
}

export function useTradingSocket() {
  const socketRef = useRef<any>(null)
  const paperRef = useRef<PaperTradingEngine | null>(null)
  const priceFeedRef = useRef<LivePriceFeed | null>(null)
  const mountedRef = useRef(false)
  const firstErrorLoggedRef = useRef(false)
  const setState = useTradingStore((s) => s.setState)
  const setConnected = useTradingStore((s) => s.setConnected)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    mountedRef.current = true
    setReady(true)
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    if (!ready) return

    let socket: any = null
    let demoTimeout: ReturnType<typeof setTimeout> | null = null

    const applyState = (data: any) => {
      if (!mountedRef.current) return
      setState({
        isRunning: data.is_running,
        // v82j+: capture engine version for snapshot traceability.
        // Falls back to null if engine doesn't send it (older builds).
        engineVersion: data.engine_version ?? null,
        currentPrice: data.current_price,
        symbol: data.symbol,
        timeframe: data.timeframe,
        exchange: data.exchange,
        patternBuffer: data.pattern_buffer || [],
        entropy: data.entropy,
        regime: data.regime,
        latestSignal: data.latest_signal,
        signalsHistory: data.signals_history || [],
        positions: data.positions || [],
        portfolioValue: data.portfolio_value,
        cash: data.cash,
        unrealizedPnl: data.unrealized_pnl,
        realizedPnl: data.realized_pnl,
        totalPnlPct: data.total_pnl_pct,
        exposurePct: data.exposure_pct,
        dailyReturnPct: data.daily_return_pct,
        leverage: data.leverage,
        autoMode: data.auto_mode,
        circuitBreakers: data.circuit_breakers || {},
        isTradingAllowed: data.is_trading_allowed,
        killSwitchActive: data.kill_switch_active,
        maxDrawdownPct: data.max_drawdown_pct,
        dailyLossPct: data.daily_loss_pct,
        totalTrades: data.total_trades,
        winningTrades: data.winning_trades,
        winRate: data.win_rate,
        maxDrawdown: data.max_drawdown,
        equityCurve: data.equity_curve || [],
        equityTimestamps: data.equity_timestamps || [],
        monteCarlo: data.monte_carlo,
        livingTrieStats: data.living_trie_stats,
        tradeHistory: data.trade_history || [],
        candlesProcessed: data.candles_processed,
        lastTickAt: data.last_tick_at || 0,
        tickCount: data.tick_count || 0,
        websocketStatus: data.websocket_status,
        isConnected: true,
        tokenStates: data.token_states || {},
        activeTokens: data.active_tokens || ['BTC/USDT'],
        selectedToken: data.selected_token || 'BTC/USDT',
        // Only override if engine provided a real money_manager object.
        // Falling back to undefined would wipe the store and break the
        // MoneyManager Select (the 'loop back' bug).
        ...(data.money_manager ? { moneyManager: data.money_manager } : {}),
        kellyPercent: data.kelly_percent || 0,
        suggestedPositionSize: data.suggested_position_size || 0,
        riskRewardRatio: data.risk_reward_ratio || 0,
        strategies_perf: data.strategies_perf || {},
      })
      if (mountedRef.current) {
        setConnected(true, data.mode || 'paper')
      }
    }

    // ─── Start Paper Trading Engine (singleton) ───────────────
    // Use a global singleton so StrictMode double-mount in dev doesn't
    // spawn multiple engines. We refcount so cleanup only tears down
    // when the last consumer unmounts.
    GLOBAL_REFCOUNT++
    if (!GLOBAL_ENGINE) {
      console.log('[Paper] Starting paper trading engine with live Coinbase + CoinGecko prices')
      console.log(`[Paper] Initial capital: ${INITIAL_CAPITAL} USDT`)
      console.log(`[Paper] Supported tokens: ${SUPPORTED_TOKENS.length}`)

      GLOBAL_FEED = new LivePriceFeed([...SUPPORTED_TOKENS])
      GLOBAL_ENGINE = new PaperTradingEngine(GLOBAL_FEED)
      // Auto-enable trading + auto-mode so the engine starts scanning
      // and opening positions the moment the page loads. The user can
      // still pause via the header buttons.
      GLOBAL_ENGINE.setTradingEnabled(true)
      GLOBAL_ENGINE.setAutoMode(true)
      useTradingStore.getState().setState({
        isRunning: true,
        autoMode: true,
        killSwitchActive: false,
      })
      console.log('[Paper] Auto-mode + trading enabled on init — engine will hunt opportunities continuously')
      GLOBAL_LISTENER = (state: any) => applyState(state)
      GLOBAL_ENGINE.startTicking(GLOBAL_LISTENER, 1500)
    } else {
      console.log('[Paper] Reusing existing paper engine (StrictMode remount)')
      // Replace listener so the new component gets fresh state pushes
      GLOBAL_ENGINE.stopTicking()
      GLOBAL_LISTENER = (state: any) => applyState(state)
      GLOBAL_ENGINE.startTicking(GLOBAL_LISTENER, 1500)
    }
    paperRef.current = GLOBAL_ENGINE
    priceFeedRef.current = GLOBAL_FEED

    // ─── Optional: connect to Python bridge for PPMT signals ──
    const trySocketConnection = () => {
      const bridgeUrl = process.env.NEXT_PUBLIC_BRIDGE_URL
      if (!bridgeUrl) {
        console.log('[Socket] No NEXT_PUBLIC_BRIDGE_URL set — paper mode only')
        return
      }

      import('socket.io-client').then(({ io }) => {
        if (!mountedRef.current) return
        socket = io(bridgeUrl, {
          path: '/socket.io',
          transports: ['polling', 'websocket'],
          forceNew: true,
          reconnection: true,
          reconnectionAttempts: 5,
          reconnectionDelay: 2000,
          timeout: 5000,
        })
        socketRef.current = socket

        socket.on('connect', () => {
          console.log('[Socket] Connected to Python Bridge at', bridgeUrl)
          firstErrorLoggedRef.current = false
        })

        socket.on('disconnect', () => {
          console.log('[Socket] Disconnected from Python Bridge (paper engine keeps running)')
        })

        socket.on('engine-status', (data: { connected: boolean; mode: string }) => {
          console.log('[Socket] Engine status from bridge:', data)
        })

        socket.on('trading-state', (data: any) => {
          if (data.latest_signal && mountedRef.current) {
            setState({ latestSignal: data.latest_signal })
          }
        })

        socket.on('connect_error', (err: any) => {
          if (!firstErrorLoggedRef.current) {
            console.warn(
              `[Socket] Bridge at ${bridgeUrl} unreachable: ${err.message}. ` +
              `Paper engine continues on live prices. (further retries silenced)`
            )
            firstErrorLoggedRef.current = true
          }
        })
      }).catch((err) => {
        console.error('[Socket] Failed to load socket.io-client:', err)
      })
    }

    const bridgeUrl = process.env.NEXT_PUBLIC_BRIDGE_URL
    if (bridgeUrl) {
      demoTimeout = setTimeout(() => {
        // no-op — paper engine is already running; bridge is supplementary
      }, BRIDGE_FALLBACK_DELAY)
    }

    trySocketConnection()

    return () => {
      if (demoTimeout) clearTimeout(demoTimeout)
      if (socket) {
        socket.disconnect()
        socket = null
        socketRef.current = null
      }
      // Decrement refcount; only teardown global engine when last consumer unmounts
      GLOBAL_REFCOUNT = Math.max(0, GLOBAL_REFCOUNT - 1)
      if (GLOBAL_REFCOUNT === 0) {
        console.log('[Paper] Last consumer unmounted — tearing down engine + price feed')
        if (GLOBAL_ENGINE) {
          GLOBAL_ENGINE.stopTicking()
          GLOBAL_ENGINE = null
        }
        if (GLOBAL_FEED) {
          GLOBAL_FEED.disconnect()
          GLOBAL_FEED = null
        }
        GLOBAL_LISTENER = null
      } else {
        // Just stop this consumer's ticking subscription
        if (GLOBAL_ENGINE) {
          GLOBAL_ENGINE.stopTicking()
        }
      }
      paperRef.current = null
      priceFeedRef.current = null
      firstErrorLoggedRef.current = false
    }
  }, [ready, setState, setConnected])

  const emit = useCallback((event: string, data?: any) => {
    // Prefer the singleton engine — paperRef may be stale across StrictMode
    // remounts but the singleton lives for the lifetime of the page.
    const paper = GLOBAL_ENGINE || paperRef.current
    if (!paper) {
      console.warn('[Paper] No paper engine available for event:', event)
      return
    }

    const storeUpdate = (state: any) => {
      useTradingStore.getState().setState({
        isRunning: state.is_running,
        currentPrice: state.current_price,
        symbol: state.symbol,
        timeframe: state.timeframe,
        exchange: state.exchange,
        patternBuffer: state.pattern_buffer,
        entropy: state.entropy,
        regime: state.regime,
        latestSignal: state.latest_signal,
        signalsHistory: state.signals_history || [],
        positions: state.positions || [],
        portfolioValue: state.portfolio_value,
        cash: state.cash,
        unrealizedPnl: state.unrealized_pnl,
        realizedPnl: state.realized_pnl,
        leverage: state.leverage,
        autoMode: state.auto_mode,
        circuitBreakers: state.circuit_breakers,
        isTradingAllowed: state.is_trading_allowed,
        killSwitchActive: state.kill_switch_active,
        maxDrawdownPct: state.max_drawdown_pct,
        dailyLossPct: state.daily_loss_pct,
        totalTrades: state.total_trades,
        winningTrades: state.winning_trades,
        winRate: state.win_rate,
        maxDrawdown: state.max_drawdown,
        equityCurve: state.equity_curve || [],
        equityTimestamps: state.equity_timestamps || [],
        monteCarlo: state.monte_carlo,
        livingTrieStats: state.living_trie_stats,
        tradeHistory: state.trade_history || [],
        candlesProcessed: state.candles_processed,
        lastTickAt: state.last_tick_at || 0,
        tickCount: state.tick_count || 0,
        isConnected: true,
        tokenStates: state.token_states || {},
        activeTokens: state.active_tokens || ['BTC/USDT'],
        selectedToken: state.selected_token || 'BTC/USDT',
        ...(state.money_manager ? { moneyManager: state.money_manager } : {}),
        kellyPercent: state.kelly_percent || 0,
        suggestedPositionSize: state.suggested_position_size || 0,
        riskRewardRatio: state.risk_reward_ratio || 0,
        strategies_perf: state.strategies_perf || {},
      })
    }

    switch (event) {
      case 'start-trading':
        paper.setTradingEnabled(true)
        useTradingStore.getState().setState({ isRunning: true, killSwitchActive: false })
        console.log('[Paper] Trading enabled — new entries allowed')
        break
      case 'stop-trading':
        paper.setTradingEnabled(false)
        useTradingStore.getState().setState({ isRunning: false })
        console.log('[Paper] Trading paused — no new entries (positions remain open)')
        break
      case 'kill-switch':
        paper.killSwitch()
        useTradingStore.getState().setState({ killSwitchActive: true, isRunning: false })
        console.log('[Paper] Kill switch — all positions closed, new entries disabled')
        break
      case 'toggle-auto':
        paper.setAutoMode(!!data?.enabled)
        useTradingStore.getState().setState({ autoMode: !!data?.enabled })
        break
      case 'switch-symbol':
        if (data?.symbol) {
          paper.setSymbol(data.symbol)
          useTradingStore.getState().setState({ selectedToken: data.symbol, symbol: data.symbol })
          console.log('[Paper] Selected symbol:', data.symbol)
        }
        break
      case 'switch-timeframe':
        paper.setTimeframe(data?.timeframe || 'live')
        break
      case 'toggle-token': {
        const store = useTradingStore.getState()
        const isActive = store.activeTokens.includes(data.symbol)
        const newActive = isActive
          ? store.activeTokens.filter(s => s !== data.symbol)
          : [...store.activeTokens, data.symbol]
        paper.setActiveTokens(newActive)
        useTradingStore.getState().setState({ activeTokens: newActive })
        console.log('[Paper] Token toggled:', data.symbol, isActive ? 'off' : 'on')
        break
      }
      case 'update-money-manager':
        if (data) {
          paper.setMoneyManager(data)
          useTradingStore.getState().updateMoneyManager(data)
          console.log('[Paper] Money manager updated')
        }
        break
      // ─── NEW: Manual trading events ────────────────────
      case 'manual-buy': {
        const sym = data?.symbol || useTradingStore.getState().selectedToken
        const amt = Number(data?.amount) || 100
        const result = paper.marketBuy(sym, amt)
        if (result.success) {
          console.log(`[Paper] BUY ${sym} ${result.qty?.toFixed(6)} @ ${result.fillPrice}`)
        } else {
          console.warn(`[Paper] BUY failed: ${result.error}`)
        }
        // Force immediate snapshot so UI reflects the new position
        storeUpdate((paper as any).snapshot?.() || {})
        break
      }
      case 'manual-sell': {
        const sym = data?.symbol || useTradingStore.getState().selectedToken
        const amt = Number(data?.amount) || 100
        const result = paper.marketSell(sym, amt)
        if (result.success) {
          console.log(`[Paper] SELL ${sym} ${result.qty?.toFixed(6)} @ ${result.fillPrice} (pnl ${result.pnl?.toFixed(2)})`)
        } else {
          console.warn(`[Paper] SELL failed: ${result.error}`)
        }
        break
      }
      case 'close-position': {
        const sym = data?.symbol || useTradingStore.getState().selectedToken
        const result = paper.closePosition(sym)
        if (result.success) {
          console.log(`[Paper] CLOSED ${sym} @ ${result.fillPrice} (pnl ${result.pnl?.toFixed(2)})`)
        } else {
          console.warn(`[Paper] CLOSE failed: ${result.error}`)
        }
        break
      }
      default:
        console.warn('[Paper] Unknown event:', event)
    }
  }, [])

  return { emit }
}
