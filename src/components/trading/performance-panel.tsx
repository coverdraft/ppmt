/**
 * PerformancePanel — Win rate, profit factor, Monte Carlo, equity curve.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { TrendingUp, TrendingDown, Target, Shield, BarChart3, Dice5 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'

export function PerformancePanel() {
  const {
    totalTrades,
    winningTrades,
    winRate,
    maxDrawdown,
    maxDrawdownPct,
    monteCarlo,
    equityCurve,
    equityTimestamps,
    realizedPnl,
    dailyLossPct,
  } = useTradingStore()

  // Equity curve chart (simple SVG)
  const recentEquity = equityCurve.slice(-100)
  const eqMin = Math.min(...recentEquity, 1000)
  const eqMax = Math.max(...recentEquity, 1000)
  const eqRange = eqMax - eqMin || 1

  const chartH = 60
  const chartW = 300

  const points = recentEquity.map((v, i) => {
    const x = (i / (recentEquity.length - 1)) * chartW
    const y = chartH - ((v - eqMin) / eqRange) * (chartH - 10) - 5
    return `${x},${y}`
  }).join(' ')

  // Area fill
  const areaPoints = points +
    ` ${chartW},${chartH} 0,${chartH}`

  const isPositive = recentEquity.length > 1 && recentEquity[recentEquity.length - 1] >= recentEquity[0]
  const lineColor = isPositive ? '#10b981' : '#ef4444'
  const fillColor = isPositive ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)'

  // Profit factor estimate
  const profitFactor = winRate > 0 && winRate < 1
    ? ((winRate * 2.5) / (1 - winRate)).toFixed(2)
    : winRate >= 1 ? '∞' : '--'

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <BarChart3 className="w-4 h-4 text-purple-400" />
          <span className="text-gray-200 font-mono">PERFORMANCE</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3 space-y-3">
        {/* Key Metrics */}
        <div className="grid grid-cols-3 gap-2">
          <div className="text-center">
            <div className="text-lg font-bold text-white font-mono">
              {winRate > 0 ? (winRate * 100).toFixed(1) : '0'}%
            </div>
            <div className="text-[9px] text-gray-500 font-mono">WIN RATE</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-bold text-white font-mono">{profitFactor}</div>
            <div className="text-[9px] text-gray-500 font-mono">PFACTOR</div>
          </div>
          <div className="text-center">
            <div className="text-lg font-bold text-white font-mono">{totalTrades}</div>
            <div className="text-[9px] text-gray-500 font-mono">TRADES</div>
          </div>
        </div>

        {/* Win/Loss bar */}
        <div>
          <div className="flex justify-between text-[10px] font-mono mb-1">
            <span className="text-emerald-400">{winningTrades}W</span>
            <span className="text-red-400">{totalTrades - winningTrades}L</span>
          </div>
          <div className="h-2 bg-red-500/30 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${winRate * 100}%` }}
            />
          </div>
        </div>

        {/* Equity Curve */}
        <div>
          <div className="text-[10px] text-gray-500 font-mono mb-1">EQUITY CURVE</div>
          <svg viewBox={`0 0 ${chartW} ${chartH}`} className="w-full h-16" preserveAspectRatio="none">
            <polygon points={areaPoints} fill={fillColor} />
            <polyline
              points={points}
              fill="none"
              stroke={lineColor}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            {/* Baseline at 1000 */}
            {(() => {
              const baseY = chartH - ((1000 - eqMin) / eqRange) * (chartH - 10) - 5
              return <line x1="0" y1={baseY} x2={chartW} y2={baseY} stroke="#1e2a3d" strokeWidth="1" strokeDasharray="4,4" vectorEffect="non-scaling-stroke" />
            })()}
          </svg>
        </div>

        {/* Max Drawdown */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1">
            <TrendingDown className="w-3 h-3 text-red-400" />
            <span className="text-[10px] text-gray-500 font-mono">MAX DD</span>
          </div>
          <span className="text-xs text-red-400 font-mono">{maxDrawdownPct.toFixed(1)}%</span>
        </div>

        {/* Monte Carlo */}
        {monteCarlo && (
          <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
            <div className="flex items-center gap-1 mb-1">
              <Dice5 className="w-3 h-3 text-purple-400" />
              <span className="text-[10px] text-gray-500 font-mono">MONTE CARLO</span>
              <Badge
                variant="outline"
                className={`text-[8px] font-mono ml-auto px-1 py-0 ${
                  monteCarlo.verdict === 'PASS'
                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    : 'bg-red-500/20 text-red-400 border-red-500/30'
                }`}
              >
                {monteCarlo.verdict}
              </Badge>
            </div>
            <div className="grid grid-cols-3 gap-1 text-center text-[9px] font-mono">
              <div>
                <div className="text-gray-300">{(monteCarlo.risk_of_ruin * 100).toFixed(1)}%</div>
                <div className="text-gray-600">RUIN</div>
              </div>
              <div>
                <div className="text-gray-300">{(monteCarlo.probability_of_profit * 100).toFixed(0)}%</div>
                <div className="text-gray-600">PROFIT</div>
              </div>
              <div>
                <div className="text-gray-300">{monteCarlo.p95_dd?.toFixed(1)}%</div>
                <div className="text-gray-600">P95 DD</div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

