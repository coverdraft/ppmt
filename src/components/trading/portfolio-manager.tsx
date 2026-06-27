/**
 * PortfolioManager — Premium portfolio visualization with allocation donut,
 * per-token performance cards, and portfolio-level metrics.
 * ObservableHQ-inspired data visualization quality.
 */
'use client'

import { useTradingStore, TokenState } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Progress } from '@/components/ui/progress'
import {
  PieChart, Pie, Cell, ResponsiveContainer,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ComposedChart, Line,
} from 'recharts'
import {
  Wallet, TrendingUp, TrendingDown, Coins,
  Shield, PieChart as PieChartIcon, ArrowUpRight, ArrowDownRight,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useTradingSocket } from '@/lib/use-trading-socket'
import { INITIAL_CAPITAL } from '@/lib/paper-trading-engine'

const TOKEN_ICONS: Record<string, string> = {
  'SOL/USDT': '◎',
  'BTC/USDT': '₿',
  'ETH/USDT': 'Ξ',
  'DOGE/USDT': 'Ð',
  'AVAX/USDT': '▲',
  'ADA/USDT': '₳',
  'LINK/USDT': '⬡',
  'DOT/USDT': '●',
  'MATIC/USDT': '◇',
  'UNI/USDT': '🦄',
}

export function PortfolioManager() {
  const {
    portfolioValue, cash, realizedPnl, unrealizedPnl,
    tokenStates, activeTokens, selectedToken,
    exposurePct, totalTrades, winRate, maxDrawdown,
    equityCurve, equityTimestamps, totalPnlPct,
  } = useTradingStore()
  const { emit } = useTradingSocket()

  const tokens = Object.values(tokenStates)
  const activeTokensList = tokens.filter(t => t.isActive)
  const totalPnl = realizedPnl + unrealizedPnl
  const isPositive = totalPnl >= 0
  // totalPnlPct comes from the store (computed correctly by the engine
  // using INITIAL_CAPITAL). Previously hardcoded 1000 which gave wrong %.

  // Build donut chart data
  const allocationData = activeTokensList.map(t => ({
    name: t.symbol.replace('/USDT', ''),
    value: t.allocationPct,
    pnl: t.realizedPnl + t.unrealizedPnl,
    color: t.color,
  }))

  // Build equity data
  const equityData = equityCurve.slice(-60).map((val, i) => ({
    time: equityTimestamps.slice(-60)[i],
    value: val,
    baseline: INITIAL_CAPITAL,
  }))

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`
  }

  // Custom donut label
  const renderCustomLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent, name }: any) => {
    const RADIAN = Math.PI / 180
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5
    const x = cx + radius * Math.cos(-midAngle * RADIAN)
    const y = cy + radius * Math.sin(-midAngle * RADIAN)
    if (percent < 0.08) return null
    return (
      <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={9} fontFamily="monospace" fontWeight="bold">
        {name}
      </text>
    )
  }

  return (
    <div className="space-y-3">
      {/* ─── Portfolio Overview Card ────────────────────────── */}
      <Card className="bg-gradient-to-br from-[#0d1117] to-[#111827] border-[#1e2a3d] overflow-hidden relative">
        {/* Subtle background glow */}
        <div className={`absolute top-0 right-0 w-40 h-40 rounded-full blur-3xl opacity-10 ${isPositive ? 'bg-emerald-500' : 'bg-red-500'}`} />
        <CardHeader className="pb-1 px-4 pt-3 relative z-10">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Wallet className="w-4 h-4 text-blue-400" />
            <span className="text-gray-300 font-mono">PORTFOLIO OVERVIEW</span>
            <Badge
              variant="outline"
              className={`text-[9px] font-mono ml-auto px-2 py-0.5 ${
                isPositive
                  ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10'
                  : 'text-red-400 border-red-500/30 bg-red-500/10'
              }`}
            >
              {isPositive ? '+' : ''}{totalPnlPct.toFixed(2)}%
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 relative z-10">
          <div className="grid grid-cols-2 gap-4">
            {/* Left: Portfolio Value + Stats */}
            <div className="space-y-3">
              <div>
                <div className="text-3xl font-bold text-white font-mono tracking-tight">
                  ${portfolioValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className="flex items-center gap-1.5 mt-1">
                  {isPositive ? (
                    <ArrowUpRight className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <ArrowDownRight className="w-3.5 h-3.5 text-red-400" />
                  )}
                  <span className={`text-sm font-mono font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                    {isPositive ? '+' : ''}{totalPnl.toFixed(2)} USDT
                  </span>
                </div>
              </div>

              {/* Quick Stats Grid */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-[#121a26]/80 rounded-lg p-2">
                  <div className="text-[8px] text-gray-500 font-mono uppercase">Cash</div>
                  <div className="text-xs text-white font-mono font-bold">${cash.toFixed(0)}</div>
                </div>
                <div className="bg-[#121a26]/80 rounded-lg p-2">
                  <div className="text-[8px] text-gray-500 font-mono uppercase">Exposure</div>
                  <div className="text-xs text-white font-mono font-bold">{exposurePct.toFixed(1)}%</div>
                </div>
                <div className="bg-[#121a26]/80 rounded-lg p-2">
                  <div className="text-[8px] text-gray-500 font-mono uppercase">Win Rate</div>
                  <div className={`text-xs font-mono font-bold ${winRate > 0.55 ? 'text-emerald-400' : winRate > 0 ? 'text-yellow-400' : 'text-gray-400'}`}>
                    {winRate > 0 ? (winRate * 100).toFixed(1) + '%' : '--'}
                  </div>
                </div>
                <div className="bg-[#121a26]/80 rounded-lg p-2">
                  <div className="text-[8px] text-gray-500 font-mono uppercase">Max DD</div>
                  <div className="text-xs text-red-400 font-mono font-bold">{maxDrawdown.toFixed(1)}%</div>
                </div>
              </div>
            </div>

            {/* Right: Allocation Donut */}
            <div className="flex flex-col items-center justify-center">
              <div className="w-full h-[140px]">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={allocationData}
                      cx="50%"
                      cy="50%"
                      innerRadius={40}
                      outerRadius={62}
                      dataKey="value"
                      labelLine={false}
                      label={renderCustomLabel}
                      strokeWidth={2}
                      stroke="#0d1117"
                    >
                      {allocationData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} fillOpacity={0.85} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                      formatter={(val: number, name: string) => [`${val.toFixed(1)}%`, name]}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="text-[8px] text-gray-500 font-mono text-center">ALLOCATION</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ─── Equity Curve ──────────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-300 font-mono">EQUITY CURVE</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[100px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={equityData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="portfolioEqGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={isPositive ? '#10b981' : '#ef4444'} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={isPositive ? '#10b981' : '#ef4444'} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 8, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={['auto', 'auto']} tick={{ fontSize: 8, fill: '#5a6878' }} width={40} tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  formatter={(val: number, name: string) => [`$${val.toFixed(2)}`, name === 'value' ? 'Equity' : 'Start']}
                  labelFormatter={formatTime}
                />
                <Area type="monotone" dataKey="value" fill="url(#portfolioEqGrad)" stroke="none" />
                <Line type="monotone" dataKey="value" stroke={isPositive ? '#10b981' : '#ef4444'} strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* ─── Token Performance Cards ────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Coins className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-gray-300 font-mono">TOKEN POSITIONS</span>
            <Badge variant="outline" className="text-[9px] font-mono text-gray-400 border-gray-600 ml-auto px-1.5 py-0">
              {activeTokensList.length} active
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-2 space-y-2">
          <AnimatePresence>
            {tokens.map((token) => {
              const isActive = activeTokens.includes(token.symbol)
              const tokenPnl = token.realizedPnl + token.unrealizedPnl
              const isTokenPositive = tokenPnl >= 0
              const icon = TOKEN_ICONS[token.symbol] || '●'
              const isSelected = token.symbol === selectedToken

              return (
                <motion.div
                  key={token.symbol}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.2 }}
                  className={`rounded-lg border transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-[#1a2334] border-blue-500/30 shadow-lg shadow-blue-500/5'
                      : 'bg-[#121a26] border-[#1e2a3d] hover:border-[#2a3a5d]'
                  }`}
                  onClick={() => {
                    useTradingStore.getState().selectToken(token.symbol)
                    emit('switch-symbol', { symbol: token.symbol })
                  }}
                >
                  <div className="flex items-center gap-3 p-2.5">
                    {/* Token icon */}
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center text-lg font-bold shrink-0"
                      style={{
                        backgroundColor: `${token.color}20`,
                        color: token.color,
                        border: `1px solid ${token.color}30`,
                      }}
                    >
                      {icon}
                    </div>

                    {/* Token info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-mono font-bold text-white">
                          {token.symbol.replace('/USDT', '')}
                        </span>
                        <span className="text-[8px] text-gray-500 font-mono">/USDT</span>
                        {!isActive && (
                          <Badge variant="outline" className="text-[7px] font-mono text-gray-500 border-gray-700 px-1 py-0 ml-1">
                            INACTIVE
                          </Badge>
                        )}
                      </div>
                      <div className="text-[10px] text-gray-400 font-mono">
                        ${token.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: token.price < 1 ? 6 : 2 })}
                        <span className={`ml-1.5 ${token.change24h >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {token.change24h >= 0 ? '+' : ''}{token.change24h.toFixed(2)}%
                        </span>
                      </div>
                    </div>

                    {/* P&L */}
                    <div className="text-right shrink-0">
                      <div className={`text-xs font-mono font-bold ${isTokenPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                        {isTokenPositive ? '+' : ''}{tokenPnl.toFixed(2)}
                      </div>
                      <div className="text-[8px] text-gray-500 font-mono">
                        WR: {token.winRate > 0 ? (token.winRate * 100).toFixed(0) + '%' : '--'}
                        {' '}| {token.totalTrades} trades
                      </div>
                    </div>

                    {/* Active switch */}
                    <Switch
                      checked={isActive}
                      onCheckedChange={(checked) => {
                        emit('toggle-token', { symbol: token.symbol })
                      }}
                      className="scale-75"
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>

                  {/* Allocation bar */}
                  {isActive && (
                    <div className="px-2.5 pb-2">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-[#1a2334] rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{ width: `${token.allocationPct}%`, backgroundColor: token.color, opacity: 0.7 }}
                          />
                        </div>
                        <span className="text-[8px] text-gray-500 font-mono">{token.allocationPct}%</span>
                      </div>
                    </div>
                  )}
                </motion.div>
              )
            })}
          </AnimatePresence>
        </CardContent>
      </Card>

      {/* ─── Allocation Breakdown ───────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <PieChartIcon className="w-3.5 h-3.5 text-purple-400" />
            <span className="text-gray-300 font-mono">ALLOCATION BREAKDOWN</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-2">
          <div className="space-y-2">
            {activeTokensList.map((token) => {
              const tokenPnl = token.realizedPnl + token.unrealizedPnl
              const tokenValue = (portfolioValue * token.allocationPct) / 100
              return (
                <div key={token.symbol} className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: token.color }} />
                  <span className="text-[10px] text-gray-300 font-mono flex-1">{token.symbol.replace('/USDT', '')}</span>
                  <span className="text-[10px] text-gray-400 font-mono w-12 text-right">{token.allocationPct}%</span>
                  <span className="text-[10px] text-gray-500 font-mono w-16 text-right">${tokenValue.toFixed(0)}</span>
                  <span className={`text-[10px] font-mono w-14 text-right ${tokenPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {tokenPnl >= 0 ? '+' : ''}{tokenPnl.toFixed(2)}
                  </span>
                </div>
              )
            })}
          </div>
          {/* Total */}
          <div className="border-t border-[#1e2a3d] mt-2 pt-2 flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-sm bg-gray-500 shrink-0" />
            <span className="text-[10px] text-white font-mono font-bold flex-1">TOTAL</span>
            <span className="text-[10px] text-white font-mono w-12 text-right">100%</span>
            <span className="text-[10px] text-white font-mono w-16 text-right">${portfolioValue.toFixed(0)}</span>
            <span className={`text-[10px] font-mono font-bold w-14 text-right ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
              {isPositive ? '+' : ''}{totalPnl.toFixed(2)}
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
