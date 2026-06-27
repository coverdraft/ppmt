/**
 * PortfolioPanel — Portfolio summary with equity curve.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { TrendingUp, TrendingDown, DollarSign, Shield, BarChart3 } from 'lucide-react'

export function PortfolioPanel() {
  const {
    portfolioValue,
    cash,
    unrealizedPnl,
    realizedPnl,
    totalPnlPct,
    exposurePct,
    dailyReturnPct,
    leverage,
    equityCurve,
  } = useTradingStore()

  const pnlIsPositive = realizedPnl >= 0
  const dailyIsPositive = dailyReturnPct >= 0

  // Mini sparkline from equity curve (last 50 points)
  const recentEquity = equityCurve.slice(-50)
  const sparklineMin = Math.min(...recentEquity)
  const sparklineMax = Math.max(...recentEquity)
  const sparklineRange = sparklineMax - sparklineMin || 1

  const sparklinePoints = recentEquity.map((v, i) => {
    const x = (i / (recentEquity.length - 1)) * 100
    const y = 100 - ((v - sparklineMin) / sparklineRange) * 80
    return `${x},${y}`
  }).join(' ')

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {/* Portfolio Value */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardContent className="p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500 font-mono uppercase">Portfolio</span>
            <DollarSign className="w-3 h-3 text-gray-500" />
          </div>
          <div className="text-xl font-bold text-white font-mono">
            ${portfolioValue.toFixed(2)}
          </div>
          <div className="text-[10px] text-gray-400 font-mono mt-0.5">
            Cash: ${cash.toFixed(2)}
          </div>
        </CardContent>
      </Card>

      {/* P&L */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardContent className="p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500 font-mono uppercase">P&L Realized</span>
            {pnlIsPositive ? (
              <TrendingUp className="w-3 h-3 text-emerald-400" />
            ) : (
              <TrendingDown className="w-3 h-3 text-red-400" />
            )}
          </div>
          <div className={`text-xl font-bold font-mono ${pnlIsPositive ? 'text-emerald-400' : 'text-red-400'}`}>
            {pnlIsPositive ? '+' : ''}{realizedPnl.toFixed(2)}
          </div>
          <div className={`text-[10px] font-mono ${pnlIsPositive ? 'text-emerald-500' : 'text-red-500'}`}>
            {totalPnlPct >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%
          </div>
        </CardContent>
      </Card>

      {/* Unrealized + Exposure */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardContent className="p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500 font-mono uppercase">Unrealized</span>
            <BarChart3 className="w-3 h-3 text-gray-500" />
          </div>
          <div className={`text-xl font-bold font-mono ${unrealizedPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {unrealizedPnl >= 0 ? '+' : ''}{unrealizedPnl.toFixed(2)}
          </div>
          <div className="text-[10px] text-gray-400 font-mono mt-0.5">
            Exposure: {exposurePct.toFixed(1)}% | {leverage}x
          </div>
        </CardContent>
      </Card>

      {/* Daily Return + Sparkline */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardContent className="p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500 font-mono uppercase">Daily</span>
            <Shield className="w-3 h-3 text-gray-500" />
          </div>
          <div className={`text-xl font-bold font-mono ${dailyIsPositive ? 'text-emerald-400' : 'text-red-400'}`}>
            {dailyReturnPct >= 0 ? '+' : ''}{dailyReturnPct.toFixed(2)}%
          </div>
          {/* Mini sparkline */}
          {recentEquity.length > 2 && (
            <svg viewBox="0 0 100 100" className="w-full h-6 mt-1" preserveAspectRatio="none">
              <polyline
                points={sparklinePoints}
                fill="none"
                stroke={pnlIsPositive ? '#10b981' : '#ef4444'}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            </svg>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
