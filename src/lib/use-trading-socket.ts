/**
 * useTradingSocket — Hook to connect to PPMT Trading Bridge via Socket.io.
 *
 * Handles connection, state updates, and command emission.
 * Falls back to a built-in client-side DemoEngine when the bridge
 * is unreachable, so the terminal always has data flowing.
 * Now supports multi-token, portfolio, and money manager data.
 */
'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
import { useTradingStore } from '@/stores/trading-store'
import { DemoEngine } from '@/lib/demo-engine'

const BRIDGE_FALLBACK_DELAY = 3000 // ms before falling back to demo

export function useTradingSocket() {
  const socketRef = useRef<any>(null)
  const demoRef = useRef<DemoEngine | null>(null)
  const usingDemoRef = useRef(false)
  const mountedRef = useRef(false)
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
    let demoEngine: DemoEngine | null = null
    let demoTimeout: ReturnType<typeof setTimeout> | null = null

    // Apply trading-state data to the store (only if still mounted)
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
        // Multi-token
        tokenStates: data.token_states || {},
        activeTokens: data.active_tokens || ['SOL/USDT'],
        selectedToken: data.selected_token || 'SOL/USDT',
        // Money manager
        moneyManager: data.money_manager || undefined,
        kellyPercent: data.kelly_percent || 0,
        suggestedPositionSize: data.suggested_position_size || 0,
        riskRewardRatio: data.risk_reward_ratio || 0,
      })
    }

    // Start client-side demo engine as fallback
    const startDemoFallback = () => {
      if (usingDemoRef.current) return
      usingDemoRef.current = true
      console.log('[Demo] Starting client-side demo engine (bridge unreachable)')
      demoEngine = new DemoEngine()
      demoRef.current = demoEngine
      if (mountedRef.current) {
        setConnected(true, 'demo')
      }
      demoEngine.startTicking((state) => {
        applyState(state)
      })
    }

    // Try Socket.io connection to the bridge
    const trySocketConnection = () => {
      import('socket.io-client').then(({ io }) => {
        if (!mountedRef.current) return

        const bridgeUrl = process.env.NEXT_PUBLIC_BRIDGE_URL || undefined
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
          console.log('[Socket] Connected to Trading Bridge')
          // Stop demo if it was running
          if (demoEngine) {
            demoEngine.stopTicking()
            demoEngine = null
            demoRef.current = null
            usingDemoRef.current = false
          }
          if (demoTimeout) {
            clearTimeout(demoTimeout)
            demoTimeout = null
          }
        })

        socket.on('disconnect', () => {
          console.log('[Socket] Disconnected from Trading Bridge')
          if (!usingDemoRef.current && mountedRef.current) {
            setConnected(false, 'disconnected')
          }
        })

        socket.on('engine-status', (data: { connected: boolean; mode: string }) => {
          if (mountedRef.current) {
            setConnected(data.connected, data.mode)
          }
        })

        socket.on('trading-state', (data: any) => {
          applyState(data)
        })

        socket.on('kill-switch-activated', () => {
          if (mountedRef.current) {
            setState({ killSwitchActive: true, isRunning: false })
          }
        })

        socket.on('command-result', (data: { success: boolean; message: string }) => {
          console.log('[CMD]', data.message)
        })

        socket.on('connect_error', (err: any) => {
          console.error('[Socket] Connection error:', err.message)
          // Don't override demo state
          if (!usingDemoRef.current && mountedRef.current) {
            setConnected(false, 'disconnected')
          }
        })
      }).catch((err) => {
        console.error('[Socket] Failed to load socket.io-client:', err)
        startDemoFallback()
      })
    }

    // Schedule demo fallback in case bridge is unreachable
    demoTimeout = setTimeout(() => {
      if (!socket || !socket.connected) {
        startDemoFallback()
      }
    }, BRIDGE_FALLBACK_DELAY)

    trySocketConnection()

    return () => {
      if (demoTimeout) clearTimeout(demoTimeout)
      if (demoEngine) {
        demoEngine.stopTicking()
        demoEngine = null
        demoRef.current = null
      }
      if (socket) {
        socket.disconnect()
        socket = null
        socketRef.current = null
      }
      usingDemoRef.current = false
    }
  }, [ready, setState, setConnected])

  const emit = useCallback((event: string, data?: any) => {
    // Try socket first
    if (socketRef.current?.connected) {
      socketRef.current.emit(event, data)
      return
    }
    // Fall back to demo engine
    const demo = demoRef.current
    if (!demo) {
      console.warn('[Demo] No demo engine available for event:', event)
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
        // Multi-token
        tokenStates: state.token_states || {},
        activeTokens: state.active_tokens || ['SOL/USDT'],
        selectedToken: state.selected_token || 'SOL/USDT',
        moneyManager: state.money_manager || undefined,
        kellyPercent: state.kelly_percent || 0,
        suggestedPositionSize: state.suggested_position_size || 0,
        riskRewardRatio: state.risk_reward_ratio || 0,
      })
    }

    switch (event) {
      case 'start-trading':
        if (data?.symbol) demo.setSymbol(data.symbol)
        if (data?.timeframe) demo.setTimeframe(data.timeframe)
        // If demo isn't ticking, start it
        if (!demo.isRunning()) {
          demo.startTicking(storeUpdate)
        }
        useTradingStore.getState().setState({ isRunning: true })
        console.log('[Demo] Trading started')
        break
      case 'stop-trading':
        demo.stopTicking()
        useTradingStore.getState().setState({ isRunning: false })
        console.log('[Demo] Trading stopped')
        break
      case 'kill-switch':
        demo.killSwitch()
        demo.stopTicking()
        useTradingStore.getState().setState({ killSwitchActive: true, isRunning: false })
        console.log('[Demo] Kill switch activated')
        break
      case 'toggle-auto':
        console.log('[Demo] Auto mode toggled:', data?.enabled)
        break
      case 'switch-symbol':
        if (data?.symbol) demo.setSymbol(data.symbol)
        useTradingStore.getState().setState({ selectedToken: data?.symbol || 'SOL/USDT' })
        console.log('[Demo] Switched symbol:', data?.symbol)
        break
      case 'switch-timeframe':
        if (data?.timeframe) demo.setTimeframe(data.timeframe)
        console.log('[Demo] Switched timeframe:', data?.timeframe)
        break
      case 'toggle-token':
        if (data?.symbol) {
          const store = useTradingStore.getState()
          const isActive = store.activeTokens.includes(data.symbol)
          const newActive = isActive
            ? store.activeTokens.filter(s => s !== data.symbol)
            : [...store.activeTokens, data.symbol]
          demo.setActiveTokens(newActive)
          useTradingStore.getState().setState({ activeTokens: newActive })
          console.log('[Demo] Toggled token:', data.symbol, isActive ? 'off' : 'on')
        }
        break
      case 'update-money-manager':
        if (data) {
          demo.setMoneyManager(data)
          useTradingStore.getState().updateMoneyManager(data)
          console.log('[Demo] Money manager updated')
        }
        break
    }
  }, [])

  return { emit }
}
