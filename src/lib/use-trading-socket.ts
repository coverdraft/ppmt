/**
 * useTradingSocket — Hook for PPMT Trading Terminal.
 *
 * NEW: Uses PaperTradingEngine with LIVE Binance prices instead of DemoEngine.
 * The terminal now operates in true paper-trading mode:
 *  - Real-time prices from Binance public WebSocket (no API key)
 *  - Manual BUY / SELL / CLOSE on any of 25 supported tokens
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

export function useTradingSocket() {
  const socketRef = useRef<any>(null)
  const priceFeedRef = useRef<LivePriceFeed | null>(null)
  const paperRef = useRef<PaperTradingEngine | null>(null)
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
    let paperEngine: PaperTradingEngine | null = null
    let priceFeed: LivePriceFeed | null = null
    let demoTimeout: ReturnType<typeof setTimeout> | null = null

    const applyState = (data: any) => {
      if (!mountedRef.current) return
      setState({
        isRunning: data.is_running,
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
        websocketStatus: data.websocket_status,
        isConnected: true,
        tokenStates: data.token_states || {},
        activeTokens: data.active_tokens || ['BTC/USDT'],
        selectedToken: data.selected_token || 'BTC/USDT',
        moneyManager: data.money_manager || undefined,
        kellyPercent: data.kelly_percent || 0,
        suggestedPositionSize: data.suggested_position_size || 0,
        riskRewardRatio: data.risk_reward_ratio || 0,
      })
      // Set connection status: paper engine always reports connected once
      // we have a price feed, even if the Python bridge is not running.
      if (mountedRef.current) {
        setConnected(true, data.mode || 'paper')
      }
    }

    // ─── Start Paper Trading Engine with Live Prices ──────────
    const startPaperEngine = () => {
      if (paperEngine) return
      console.log('[Paper] Starting paper trading engine with live Binance prices')
      console.log(`[Paper] Initial capital: ${INITIAL_CAPITAL} USDT`)
      console.log(`[Paper] Supported tokens: ${SUPPORTED_TOKENS.length}`)

      // LivePriceFeed subscribes to all supported tokens by default
      priceFeed = new LivePriceFeed([...SUPPORTED_TOKENS])
      priceFeedRef.current = priceFeed

      paperEngine = new PaperTradingEngine(priceFeed)
      paperRef.current = paperEngine

      paperEngine.startTicking((state) => {
        applyState(state)
      }, 1500)
    }

    startPaperEngine()

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
          // Don't override paper's connected status — bridge is supplementary
          console.log('[Socket] Engine status from bridge:', data)
        })

        socket.on('trading-state', (data: any) => {
          // Bridge can supplement with PPMT signals — merge with paper state
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
      if (paperEngine) {
        paperEngine.stopTicking()
        paperEngine = null
        paperRef.current = null
      }
      if (priceFeed) {
        priceFeed.disconnect()
        priceFeed = null
        priceFeedRef.current = null
      }
      if (socket) {
        socket.disconnect()
        socket = null
        socketRef.current = null
      }
      firstErrorLoggedRef.current = false
    }
  }, [ready, setState, setConnected])

  const emit = useCallback((event: string, data?: any) => {
    const paper = paperRef.current
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
        isConnected: true,
        tokenStates: state.token_states || {},
        activeTokens: state.active_tokens || ['BTC/USDT'],
        selectedToken: state.selected_token || 'BTC/USDT',
        moneyManager: state.money_manager || undefined,
        kellyPercent: state.kelly_percent || 0,
        suggestedPositionSize: state.suggested_position_size || 0,
        riskRewardRatio: state.risk_reward_ratio || 0,
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
