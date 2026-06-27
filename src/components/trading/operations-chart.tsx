/**
 * OperationsChart — ObservableHQ-style visualization for live trading operations.
 *
 * Shows:
 *  1. Price chart with entry/exit trade markers
 *  2. Running P&L curve
 *  3. Trade results distribution (waterfall)
 *  4. Position lifecycle visualization
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { INITIAL_CAPITAL } from '@/lib/paper-trading-engine'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Scatter, Cell, ReferenceLine,
  BarChart, Bar,
} from 'recharts'
import { CandlestickChart, TrendingUp, TrendingDown, Activity, DollarSign } from 'lucide-react'

export function OperationsChart() {
  const {
    priceHistory, equityCurve, equityTimestamps,
    tradeHistory, positions, currentPrice, symbol,
    realizedPnl, unrealizedPnl, totalTrades,
  } = useTradingStore()

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`
  }

  const formatPrice = (p: number) => `$${p.toFixed(2)}`

  // Build price + trades combined data
  const priceWithTrades = priceHistory.map((p) => ({
    ...p,
    // Mark trades on price chart
    tradeMarker: p.isTrade ? p.price : undefined,
  }))

  // Build equity curve data for recharts
  const equityData = equityCurve.map((val, i) => ({
    time: equityTimestamps[i],
    value: val,
    baseline: INITIAL_CAPITAL,
  }))

  // Build trade PnL bars
  const tradePnlData = tradeHistory.slice(0, 20).reverse().map((t, i) => ({
    idx: i + 1,
    pnl: t.pnl_usdt,
    pnlPct: t.pnl_pct,
    direction: t.direction,
    reason: t.close_reason?.replace('CLOSED_BY_', '').replace('CLOSED_', '') || '',
  }))

  const totalPnl = realizedPnl + unrealizedPnl
  const isPositive = totalPnl >= 0

  return (
    <div className="space-y-3">
      {/* ─── Price Chart with Trade Markers ─────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <CandlestickChart className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-gray-300 font-mono">PRICE & TRADES</span>
            <Badge variant="outline" className="text-[9px] font-mono text-gray-400 border-gray-600 ml-auto px-1.5 py-0">
              {symbol}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={priceWithTrades} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9, fill: '#5a6878' }} width={50} tickFormatter={formatPrice} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number, name: string) => {
                    if (name === 'price') return [`$${val.toFixed(4)}`, 'Price']
                    return [val, name]
                  }}
                />
                <Area type="monotone" dataKey="price" fill="url(#priceGrad)" stroke="none" />
                <Line type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={1.5} dot={false} />

                {/* Position TP/SL lines if active (skip if null — manual entries) */}
                {positions && positions.length > 0 && positions.map((pos, idx) => {
                  const tpColor = '#10b981'
                  const slColor = '#ef4444'
                  const lines: any[] = []
                  if (pos.current_tp !== null && pos.current_tp !== undefined) {
                    lines.push(<ReferenceLine key={`tp-${idx}`} y={pos.current_tp} stroke={tpColor} strokeDasharray="4 4" strokeOpacity={0.6} />)
                  }
                  if (pos.current_sl !== null && pos.current_sl !== undefined) {
                    lines.push(<ReferenceLine key={`sl-${idx}`} y={pos.current_sl} stroke={slColor} strokeDasharray="4 4" strokeOpacity={0.6} />)
                  }
                  return lines
                })}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Position markers legend */}
          {positions && positions.length > 0 && (
            <div className="flex items-center gap-3 text-[9px] font-mono mt-1 px-1">
              <div className="flex items-center gap-1">
                <div className="w-3 h-0.5 bg-emerald-500" style={{ borderTop: '2px dashed #10b981' }} />
                <span className="text-emerald-500">TP</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-0.5 bg-red-500" style={{ borderTop: '2px dashed #ef4444' }} />
                <span className="text-red-500">SL</span>
              </div>
              <span className="text-gray-600">|</span>
              <span className={positions[0].direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}>
                {positions[0].direction} @ ${positions[0].entry_price.toFixed(2)}
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ─── Equity Curve ────────────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-300 font-mono">EQUITY CURVE</span>
            <Badge
              variant="outline"
              className={`text-[9px] font-mono ml-auto px-1.5 py-0 ${
                isPositive
                  ? 'text-emerald-400 border-emerald-500/30'
                  : 'text-red-400 border-red-500/30'
              }`}
            >
              {isPositive ? '+' : ''}{totalPnl.toFixed(2)}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[130px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={equityData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="eqGradPos" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="eqGradNeg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9, fill: '#5a6878' }} width={45} tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number, name: string) => {
                    if (name === 'value') return [`$${val.toFixed(2)}`, 'Equity']
                    if (name === 'baseline') return [`$${val.toFixed(0)}`, 'Start']
                    return [val, name]
                  }}
                />
                {/* Baseline at INITIAL_CAPITAL */}
                <ReferenceLine y={INITIAL_CAPITAL} stroke="#1e2a3d" strokeWidth={1} />
                <Area type="monotone" dataKey="value" fill={isPositive ? 'url(#eqGradPos)' : 'url(#eqGradNeg)'} stroke="none" />
                <Line type="monotone" dataKey="value" stroke={isPositive ? '#10b981' : '#ef4444'} strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* ─── Trade Results Distribution ──────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Activity className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-gray-300 font-mono">TRADE P&L DISTRIBUTION</span>
            <Badge variant="outline" className="text-[9px] font-mono text-gray-400 border-gray-600 ml-auto px-1.5 py-0">
              {totalTrades} trades
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[100px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={tradePnlData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="idx" tick={{ fontSize: 8, fill: '#5a6878' }} />
                <YAxis tick={{ fontSize: 9, fill: '#5a6878' }} width={35} tickFormatter={(v: number) => `$${v.toFixed(1)}`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  formatter={(val: number, name: string) => {
                    if (name === 'pnl') return [`$${val.toFixed(4)}`, 'P&L']
                    return [val, name]
                  }}
                  labelFormatter={(label: number) => `Trade #${label}`}
                />
                <ReferenceLine y={0} stroke="#1e2a3d" />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {tradePnlData.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={entry.pnl >= 0 ? '#10b981' : '#ef4444'}
                      fillOpacity={0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex gap-3 text-[9px] font-mono mt-1 px-1">
            <div className="flex items-center gap-1">
              <div className="w-2 h-2 rounded bg-emerald-500" />
              <span className="text-gray-500">Win</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="w-2 h-2 rounded bg-red-500" />
              <span className="text-gray-500">Loss</span>
            </div>
            <span className="text-gray-600">|</span>
            <span className="text-gray-500">Each bar = 1 trade</span>
          </div>
        </CardContent>
      </Card>

      {/* ─── Active Position Lifecycle ───────────────────────── */}
      {positions && positions.length > 0 && (
        <Card className="bg-[#0d1117] border-[#1e2a3d]">
          <CardHeader className="pb-1 px-3 pt-2">
            <CardTitle className="flex items-center gap-2 text-xs">
              <TrendingUp className="w-3.5 h-3.5 text-cyan-400" />
              <span className="text-gray-300 font-mono">POSITION LIFECYCLE</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="px-3 pb-2">
            {positions.map((pos, idx) => {
              const isLong = pos.direction === 'LONG'
              const isPositive = pos.pnl_pct >= 0
              // Null-safe: manual entries have no SL/TP
              const tpDist = pos.current_tp !== null && currentPrice > 0
                ? (isLong
                    ? ((pos.current_tp - currentPrice) / currentPrice) * 100
                    : ((currentPrice - pos.current_tp) / currentPrice) * 100)
                : null
              const slDist = pos.current_sl !== null && currentPrice > 0
                ? (isLong
                    ? ((currentPrice - pos.current_sl) / currentPrice) * 100
                    : ((pos.current_sl - currentPrice) / currentPrice) * 100)
                : null

              // Progress bar: how far between SL and TP (only if both exist)
              const totalRange = (tpDist !== null && slDist !== null) ? Math.abs(tpDist) + Math.abs(slDist) : 0
              const progressPct = totalRange > 0 ? (Math.abs(slDist!) / totalRange) * 100 : 50

              return (
                <div key={idx} className="space-y-2">
                  {/* Price position between SL and TP */}
                  <div>
                    <div className="flex justify-between text-[9px] font-mono mb-0.5">
                      <span className="text-red-400">SL {slDist !== null ? slDist.toFixed(2) + '%' : '—'}</span>
                      <span className={isPositive ? 'text-emerald-400' : 'text-red-400'}>
                        {isPositive ? '+' : ''}{pos.pnl_pct.toFixed(3)}%
                      </span>
                      <span className="text-emerald-400">TP {tpDist !== null ? tpDist.toFixed(2) + '%' : '—'}</span>
                    </div>
                    <div className="relative h-3 bg-[#1a2334] rounded-full overflow-hidden">
                      {/* SL zone (only if SL exists) */}
                      {slDist !== null && tpDist !== null && (
                        <>
                          <div className="absolute left-0 top-0 h-full bg-red-500/20" style={{ width: `${Math.max(5, Math.min(slDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />
                          <div className="absolute right-0 top-0 h-full bg-emerald-500/20" style={{ width: `${Math.max(5, Math.min(tpDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />
                        </>
                      )}
                      {/* Current price indicator */}
                      <div
                        className="absolute top-0 h-full w-0.5 bg-white z-10"
                        style={{ left: `${progressPct}%` }}
                      />
                      {(slDist === null || tpDist === null) && (
                        <div className="absolute inset-0 flex items-center justify-center text-[8px] text-gray-500 font-mono">
                          manual entry — no SL/TP set
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Walk-forward steps */}
                  {pos.expected_sequence && pos.expected_sequence.length > 0 && (
                    <div>
                      <div className="text-[9px] text-gray-500 font-mono mb-1">WALK-FORWARD SEQUENCE</div>
                      <div className="flex gap-1">
                        {pos.expected_sequence.map((step, stepIdx) => {
                          const isMatched = stepIdx < (pos.sequence_index || 0)
                          const isCurrent = stepIdx === (pos.sequence_index || 0)
                          return (
                            <div
                              key={stepIdx}
                              className={`flex items-center gap-0.5 text-[8px] font-mono px-1 py-0.5 rounded ${
                                isMatched
                                  ? 'bg-emerald-500/20 text-emerald-400'
                                  : isCurrent
                                  ? 'bg-blue-500/20 text-blue-400 ring-1 ring-blue-400/40'
                                  : 'bg-[#1a2334] text-gray-600'
                              }`}
                            >
                              {step.join('')}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* Status badges */}
                  <div className="flex items-center gap-2 text-[9px] font-mono">
                    <Badge
                      variant="outline"
                      className={`text-[8px] px-1 py-0 ${
                        pos.status === 'ACTIVE' ? 'bg-blue-500/20 text-blue-400 border-blue-500/30' :
                        pos.status === 'BREAK_EVEN_SECURED' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                        'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      }`}
                    >
                      {pos.status}
                    </Badge>
                    <span className="text-gray-600">
                      {isLong ? '▲' : '▼'} {pos.direction}
                    </span>
                    <span className="text-gray-600 ml-auto">
                      Entry: ${pos.entry_price.toFixed(2)}
                    </span>
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
