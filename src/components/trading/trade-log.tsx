/**
 * TradeLog — Recent trades and signal history feed.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { History, ArrowUp, ArrowDown, AlertCircle, CandlestickChart } from 'lucide-react'

export function TradeLog() {
  const { tradeHistory, signalsHistory, setChartModalTrade } = useTradingStore()

  const closeReasonColors: Record<string, string> = {
    CLOSED_BY_TP: 'bg-emerald-500/20 text-emerald-400',
    CLOSED_BY_SL: 'bg-red-500/20 text-red-400',
    CLOSED_CATASTROPHIC: 'bg-red-600/20 text-red-300',
    CLOSED_KILL_SWITCH: 'bg-orange-500/20 text-orange-400',
    CLOSED_DIVERGENCE: 'bg-yellow-500/20 text-yellow-400',
  }

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <History className="w-4 h-4 text-cyan-400" />
          <span className="text-gray-200 font-mono">TRADE LOG</span>
          <Badge variant="outline" className="text-[9px] font-mono text-gray-400 border-gray-600 ml-auto px-1.5">
            {tradeHistory.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-0">
        <div className="text-[9px] text-gray-600 font-mono mb-1 italic">
          ↳ Click any trade to view chart
        </div>
        <ScrollArea className="h-[260px]">
          {tradeHistory.length > 0 ? (
            <div className="space-y-1">
              {tradeHistory.map((trade, idx) => {
                const isLong = trade.direction === 'LONG'
                const isWin = trade.pnl_pct > 0
                const reasonColor = closeReasonColors[trade.close_reason || ''] || 'bg-gray-500/20 text-gray-400'

                return (
                  <div
                    key={idx}
                    onClick={() => setChartModalTrade(trade)}
                    title="Click to open candlestick chart"
                    className={`flex items-center gap-2 py-1.5 px-2 rounded text-[10px] font-mono border cursor-pointer transition-colors ${
                      idx === 0 ? 'border-[#1e2a3d] bg-[#121a26]' : 'border-transparent'
                    } hover:border-blue-500/40 hover:bg-[#121a26]`}
                  >
                    {isLong ? (
                      <ArrowUp className="w-3 h-3 text-emerald-400 shrink-0" />
                    ) : (
                      <ArrowDown className="w-3 h-3 text-red-400 shrink-0" />
                    )}
                    <span className="text-gray-400 w-12">{trade.symbol?.split('/')[0] || '--'}</span>
                    <span className={`px-1 rounded text-[8px] ${reasonColor}`}>
                      {(trade.close_reason || '').replace('CLOSED_BY_', '').replace('CLOSED_', '')}
                    </span>
                    <CandlestickChart className="w-3 h-3 text-gray-600 ml-auto shrink-0" />
                    <span className={`${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isWin ? '+' : ''}{trade.pnl_pct?.toFixed(3)}%
                    </span>
                    <span className={`w-14 text-right ${isWin ? 'text-emerald-500' : 'text-red-500'}`}>
                      {isWin ? '+' : ''}{trade.pnl_usdt?.toFixed(2)}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center py-8">
              <History className="w-8 h-8 text-gray-700 mx-auto mb-2" />
              <div className="text-xs text-gray-500 font-mono">No trades yet</div>
              <div className="text-[10px] text-gray-600 font-mono mt-1">Trades will appear here</div>
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
