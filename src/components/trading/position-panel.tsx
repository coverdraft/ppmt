/**
 * PositionPanel — Active positions and position management.
 * Now with manual CLOSE button on each position (paper trading).
 */
'use client'

import { useState } from 'react'
import { useTradingStore } from '@/stores/trading-store'
import { useTradingSocket } from '@/lib/use-trading-socket'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Target, ArrowUp, ArrowDown, ShieldAlert, X, CandlestickChart } from 'lucide-react'

export function PositionPanel() {
  const { positions, symbol, currentPrice, setChartModalTrade } = useTradingStore()
  const { emit } = useTradingSocket()
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null)

  const handleClose = (sym: string) => {
    setClosingSymbol(sym)
    emit('close-position', { symbol: sym })
    setTimeout(() => setClosingSymbol(null), 800)
  }

  const hasPosition = positions && positions.length > 0

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Target className="w-4 h-4 text-amber-400" />
          <span className="text-gray-200 font-mono">POSITION</span>
          {hasPosition && (
            <Badge
              variant="outline"
              className="text-[9px] font-mono bg-emerald-500/20 text-emerald-400 border-emerald-500/30 px-1.5 py-0"
            >
              ACTIVE
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        {hasPosition ? (
          positions.map((pos, idx) => {
            const isLong = pos.direction === 'LONG'
            const pnlPct = pos.pnl_pct || 0
            const pnlUsdt = pos.pnl_usdt || 0
            const isPositive = pnlPct >= 0

            // Distance to TP/SL — null-safe (manual entries have no SL/TP)
            const distToTP = pos.current_tp !== null && currentPrice > 0
              ? (isLong
                  ? ((pos.current_tp - currentPrice) / currentPrice) * 100
                  : ((currentPrice - pos.current_tp) / currentPrice) * 100)
              : null
            const distToSL = pos.current_sl !== null && currentPrice > 0
              ? (isLong
                  ? ((currentPrice - pos.current_sl) / currentPrice) * 100
                  : ((pos.current_sl - currentPrice) / currentPrice) * 100)
              : null

            return (
              <div
                key={idx}
                className="space-y-2 cursor-pointer transition-all hover:bg-[#121a26]/50 -mx-1 px-1 py-1 rounded group"
                onClick={(e) => {
                  // Don't open chart if clicking the CLOSE button
                  if ((e.target as HTMLElement).closest('button')) return
                  setChartModalTrade(pos)
                }}
              >
                {/* Direction + Symbol + Chart button */}
                <div className="flex items-center gap-2">
                  {isLong ? (
                    <ArrowUp className="w-4 h-4 text-emerald-400" />
                  ) : (
                    <ArrowDown className="w-4 h-4 text-red-400" />
                  )}
                  <Badge
                    variant="outline"
                    className={`text-[10px] font-mono ${
                      isLong
                        ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                        : 'bg-red-500/20 text-red-400 border-red-500/30'
                    }`}
                  >
                    {pos.direction}
                  </Badge>
                  <span className="text-xs text-gray-300 font-mono">{pos.symbol || symbol}</span>
                  <Badge variant="outline" className="text-[9px] font-mono text-gray-400 border-gray-600 ml-auto px-1.5">
                    {pos.status}
                  </Badge>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      setChartModalTrade(pos)
                    }}
                    title="Open candlestick chart"
                    className="text-gray-500 hover:text-blue-400 transition-colors"
                  >
                    <CandlestickChart className="w-3.5 h-3.5" />
                  </button>
                </div>

                {/* Entry Price */}
                <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] font-mono">
                    <div>
                      <span className="text-gray-500">Entry</span>
                      <div className="text-gray-300">{pos.entry_price?.toFixed(4)}</div>
                    </div>
                    <div>
                      <span className="text-gray-500">Size</span>
                      <div className="text-gray-300">${pos.size_usdt?.toFixed(2)}</div>
                    </div>
                    <div>
                      <span className="text-emerald-500">TP {distToTP !== null ? distToTP.toFixed(2) + '%' : '—'}</span>
                      <div className="text-emerald-400">{pos.current_tp !== null ? pos.current_tp.toFixed(4) : 'manual'}</div>
                    </div>
                    <div>
                      <span className="text-red-500">SL {distToSL !== null ? distToSL.toFixed(2) + '%' : '—'}</span>
                      <div className="text-red-400">{pos.current_sl !== null ? pos.current_sl.toFixed(4) : 'manual'}</div>
                    </div>
                  </div>
                </div>

                {/* P&L */}
                <div className={`text-center py-2 rounded ${isPositive ? 'bg-emerald-500/10' : 'bg-red-500/10'}`}>
                  <div className={`text-2xl font-bold font-mono ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                    {isPositive ? '+' : ''}{pnlPct.toFixed(3)}%
                  </div>
                  <div className={`text-xs font-mono ${isPositive ? 'text-emerald-500' : 'text-red-500'}`}>
                    {isPositive ? '+' : ''}{pnlUsdt.toFixed(4)} USDT
                  </div>
                </div>

                {/* Walk-Forward Progress */}
                {pos.expected_sequence && pos.expected_sequence.length > 0 && (
                  <div>
                    <div className="text-[10px] text-gray-500 font-mono mb-1">
                      WALK-FORWARD {pos.sequence_index || 0}/{pos.expected_sequence.length}
                    </div>
                    <div className="h-1.5 bg-[#1a2334] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-500 rounded-full transition-all duration-300"
                        style={{ width: `${((pos.sequence_index || 0) / pos.expected_sequence.length) * 100}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Catastrophic SL warning */}
                <div className="flex items-center gap-1 text-[10px] text-gray-600 font-mono">
                  <ShieldAlert className="w-3 h-3" />
                  Cat SL: {pos.catastrophic_sl !== null ? pos.catastrophic_sl.toFixed(4) : '—'}
                </div>

                {/* Close button (paper trading) */}
                <Button
                  size="sm"
                  variant="destructive"
                  className="w-full h-7 text-[10px] font-mono"
                  disabled={closingSymbol === pos.symbol}
                  onClick={() => handleClose(pos.symbol)}
                >
                  <X className="w-3 h-3 mr-1" />
                  {closingSymbol === pos.symbol ? 'CLOSING...' : `CLOSE ${pos.symbol}`}
                </Button>
              </div>
            )
          })
        ) : (
          <div className="text-center py-8">
            <Target className="w-8 h-8 text-gray-700 mx-auto mb-2" />
            <div className="text-xs text-gray-500 font-mono">No active position</div>
            <div className="text-[10px] text-gray-600 font-mono mt-1">Waiting for signal...</div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
