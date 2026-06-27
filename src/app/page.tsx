'use client'

import { useTradingSocket } from '@/lib/use-trading-socket'
import { useTradingStore } from '@/stores/trading-store'
import { StatusHeader } from '@/components/trading/header'
import { PortfolioPanel } from '@/components/trading/portfolio-panel'
import { BrainPanel } from '@/components/trading/brain-panel'
import { PositionPanel } from '@/components/trading/position-panel'
import { ManualTradePanel } from '@/components/trading/manual-trade-panel'
import { PerformancePanel } from '@/components/trading/performance-panel'
import { TradeLog } from '@/components/trading/trade-log'
import { RiskPanel } from '@/components/trading/risk-panel'
import { SignalFeed } from '@/components/trading/signal-feed'
import { BrainChart } from '@/components/trading/brain-chart'
import { LearningChart } from '@/components/trading/learning-chart'
import { OperationsChart } from '@/components/trading/operations-chart'
import { PortfolioManager } from '@/components/trading/portfolio-manager'
import { MoneyManager } from '@/components/trading/money-manager'
import { TokenSelector } from '@/components/trading/token-selector'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useState, useEffect } from 'react'
import {
  Brain, GraduationCap, CandlestickChart, LayoutGrid,
  Wallet, Settings, Clock,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'

export default function TerminalPage() {
  const { emit } = useTradingSocket()
  const {
    isConnected,
    engineMode,
    isRunning,
    symbol,
    timeframe,
    currentPrice,
    positions,
    realizedPnl,
    totalTrades,
    winRate,
    portfolioValue,
    tokenStates,
    selectedToken,
    moneyManager,
    kellyPercent,
    suggestedPositionSize,
    riskRewardRatio,
  } = useTradingStore()

  const [selectedSymbol, setSelectedSymbol] = useState(symbol)
  const [selectedTimeframe, setSelectedTimeframe] = useState(timeframe)
  const [activeTab, setActiveTab] = useState('dashboard')
  const [currentTime, setCurrentTime] = useState('')

  // Live clock
  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentTime(new Date().toLocaleTimeString('es-ES', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      }))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  const handleStartStop = () => {
    if (isRunning) {
      emit('stop-trading')
    } else {
      emit('start-trading', { symbol: selectedSymbol, timeframe: selectedTimeframe, capital: 1000 })
    }
  }

  const handleKillSwitch = () => {
    if (confirm('⚠️ KILL SWITCH: Close all positions and stop trading?')) {
      emit('kill-switch')
    }
  }

  const handleToggleAuto = (enabled: boolean) => {
    emit('toggle-auto', { enabled })
  }

  const handleSymbolChange = (val: string) => {
    setSelectedSymbol(val)
    emit('switch-symbol', { symbol: val })
  }

  const handleTimeframeChange = (val: string) => {
    setSelectedTimeframe(val)
    emit('switch-timeframe', { timeframe: val })
  }

  const hasPosition = positions && positions.length > 0
  const pnlPositive = realizedPnl >= 0
  const activeTokens = Object.values(tokenStates).filter(t => t.isActive)

  // Page transition variants
  const pageVariants = {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: -8 },
  }

  return (
    <div className="min-h-screen bg-[#080c14] text-gray-200 flex flex-col">
      {/* Header */}
      <StatusHeader
        onStartStop={handleStartStop}
        onKillSwitch={handleKillSwitch}
        onToggleAuto={handleToggleAuto}
      />

      {/* Multi-Token Selector Bar */}
      <div className="flex items-center gap-3 px-4 py-2 bg-[#0a0e17] border-b border-[#1e2a3d]">
        <TokenSelector />

        <div className="h-4 w-px bg-[#1e2a3d] shrink-0" />

        {/* Timeframe Selector */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] text-gray-500 font-mono">TF</span>
          <Select value={selectedTimeframe} onValueChange={handleTimeframeChange}>
            <SelectTrigger className="h-7 w-20 bg-[#121a26] border-[#1e2a3d] text-xs font-mono">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-[#121a26] border-[#1e2a3d]">
              <SelectItem value="5m" className="text-xs font-mono">5m</SelectItem>
              <SelectItem value="15m" className="text-xs font-mono">15m</SelectItem>
              <SelectItem value="1h" className="text-xs font-mono">1h</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="h-4 w-px bg-[#1e2a3d] shrink-0" />

        {/* Quick Stats */}
        <div className="flex items-center gap-4 ml-auto shrink-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono">TOKENS</span>
            <span className="text-xs text-white font-mono font-bold">{activeTokens.length}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono">TRADES</span>
            <span className="text-xs text-white font-mono font-bold">{totalTrades}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono">WR</span>
            <span className={`text-xs font-mono font-bold ${winRate > 0.55 ? 'text-emerald-400' : winRate > 0 ? 'text-yellow-400' : 'text-gray-500'}`}>
              {winRate > 0 ? (winRate * 100).toFixed(1) + '%' : '--'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono">P&L</span>
            <span className={`text-xs font-mono font-bold ${pnlPositive ? 'text-emerald-400' : 'text-red-400'}`}>
              {pnlPositive ? '+' : ''}{realizedPnl.toFixed(2)}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 font-mono">POS</span>
            <Badge
              variant="outline"
              className={`text-[8px] font-mono px-1.5 py-0 ${
                hasPosition
                  ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                  : 'bg-gray-500/20 text-gray-400 border-gray-500/30'
              }`}
            >
              {hasPosition ? 'OPEN' : 'FLAT'}
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3 text-gray-600" />
            <span className="text-[10px] text-gray-500 font-mono">{currentTime}</span>
          </div>
        </div>
      </div>

      {/* Portfolio Summary Cards */}
      <div className="px-3 pt-3">
        <PortfolioPanel />
      </div>

      {/* ─── Tab Navigation ──────────────────────────────── */}
      <div className="px-3 pt-2">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="bg-[#0d1117] border border-[#1e2a3d] h-8 p-0.5">
            <TabsTrigger
              value="dashboard"
              className="text-[10px] font-mono h-7 px-3 data-[state=active]:bg-[#1a2334] data-[state=active]:text-blue-400"
            >
              <LayoutGrid className="w-3 h-3 mr-1" />
              DASHBOARD
            </TabsTrigger>
            <TabsTrigger
              value="portfolio"
              className="text-[10px] font-mono h-7 px-3 data-[state=active]:bg-[#1a2334] data-[state=active]:text-amber-400"
            >
              <Wallet className="w-3 h-3 mr-1" />
              PORTFOLIO
            </TabsTrigger>
            <TabsTrigger
              value="brain"
              className="text-[10px] font-mono h-7 px-3 data-[state=active]:bg-[#1a2334] data-[state=active]:text-cyan-400"
            >
              <Brain className="w-3 h-3 mr-1" />
              BRAIN
            </TabsTrigger>
            <TabsTrigger
              value="learning"
              className="text-[10px] font-mono h-7 px-3 data-[state=active]:bg-[#1a2334] data-[state=active]:text-purple-400"
            >
              <GraduationCap className="w-3 h-3 mr-1" />
              LEARNING
            </TabsTrigger>
            <TabsTrigger
              value="operations"
              className="text-[10px] font-mono h-7 px-3 data-[state=active]:bg-[#1a2334] data-[state=active]:text-emerald-400"
            >
              <CandlestickChart className="w-3 h-3 mr-1" />
              OPERATIONS
            </TabsTrigger>
          </TabsList>

          {/* ─── Dashboard Tab (original layout) ──────────── */}
          <TabsContent value="dashboard" className="mt-3">
            <motion.div
              key="dashboard"
              initial="initial"
              animate="animate"
              exit="exit"
              variants={pageVariants}
              transition={{ duration: 0.2 }}
            >
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                {/* Left Column: Brain + Risk */}
                <div className="lg:col-span-3 space-y-3">
                  <BrainPanel />
                  <RiskPanel />
                </div>

                {/* Center Column: Position + Manual Trade + Performance */}
                <div className="lg:col-span-5 space-y-3">
                  <PositionPanel />
                  <ManualTradePanel />
                  <PerformancePanel />
                </div>

                {/* Right Column: Signals + Trade Log */}
                <div className="lg:col-span-4 space-y-3">
                  <SignalFeed />
                  <TradeLog />
                </div>
              </div>
            </motion.div>
          </TabsContent>

          {/* ─── Portfolio Tab (NEW) ──────────────────────── */}
          <TabsContent value="portfolio" className="mt-3">
            <motion.div
              key="portfolio"
              initial="initial"
              animate="animate"
              exit="exit"
              variants={pageVariants}
              transition={{ duration: 0.2 }}
            >
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                {/* Left: Portfolio Manager */}
                <div className="lg:col-span-7">
                  <PortfolioManager />
                </div>
                {/* Right: Money Manager */}
                <div className="lg:col-span-5">
                  <MoneyManager />
                </div>
              </div>
            </motion.div>
          </TabsContent>

          {/* ─── Brain Tab ────────────────────────────────── */}
          <TabsContent value="brain" className="mt-3">
            <motion.div
              key="brain"
              initial="initial"
              animate="animate"
              exit="exit"
              variants={pageVariants}
              transition={{ duration: 0.2 }}
            >
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                {/* Brain Charts */}
                <div className="lg:col-span-8">
                  <BrainChart />
                </div>
                {/* Brain Summary */}
                <div className="lg:col-span-4 space-y-3">
                  <BrainPanel />
                  <RiskPanel />
                </div>
              </div>
            </motion.div>
          </TabsContent>

          {/* ─── Learning Tab ─────────────────────────────── */}
          <TabsContent value="learning" className="mt-3">
            <motion.div
              key="learning"
              initial="initial"
              animate="animate"
              exit="exit"
              variants={pageVariants}
              transition={{ duration: 0.2 }}
            >
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                {/* Learning Charts */}
                <div className="lg:col-span-8">
                  <LearningChart />
                </div>
                {/* Performance + Signals */}
                <div className="lg:col-span-4 space-y-3">
                  <PerformancePanel />
                  <SignalFeed />
                </div>
              </div>
            </motion.div>
          </TabsContent>

          {/* ─── Operations Tab ───────────────────────────── */}
          <TabsContent value="operations" className="mt-3">
            <motion.div
              key="operations"
              initial="initial"
              animate="animate"
              exit="exit"
              variants={pageVariants}
              transition={{ duration: 0.2 }}
            >
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
                {/* Operations Charts */}
                <div className="lg:col-span-8">
                  <OperationsChart />
                </div>
                {/* Position + Trade Log */}
                <div className="lg:col-span-4 space-y-3">
                  <PositionPanel />
                  <TradeLog />
                </div>
              </div>
            </motion.div>
          </TabsContent>
        </Tabs>
      </div>

      {/* Footer Status Bar */}
      <footer className="flex items-center justify-between px-4 py-1.5 bg-[#0a0e17] border-t border-[#1e2a3d] mt-auto">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
            <span className="text-[9px] text-gray-500 font-mono">
              {isConnected ? 'CONNECTED' : 'DISCONNECTED'}
            </span>
          </div>
          <span className="text-[9px] text-gray-600 font-mono">|</span>
          <span className={`text-[9px] font-mono ${engineMode === 'paper' ? 'text-blue-400' : 'text-gray-500'}`}>
            Engine: {engineMode.toUpperCase()}
          </span>
          <span className="text-[9px] text-gray-600 font-mono">|</span>
          <span className="text-[9px] text-gray-500 font-mono">
            Sizing: {moneyManager.positionSizingMethod.replace('_', ' ').toUpperCase()}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[9px] text-gray-600 font-mono">
            PPMT v0.70 • PAPER TRADING • Live Binance Prices • 25 Tokens
          </span>
          <span className="text-[9px] text-gray-600 font-mono">|</span>
          <span className="text-[9px] text-gray-500 font-mono">
            {currentTime}
          </span>
        </div>
      </footer>
    </div>
  )
}
