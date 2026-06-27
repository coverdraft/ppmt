/**
 * SignalFeed — Real-time signal feed with live updates.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Radio, ArrowUp, ArrowDown, Zap } from 'lucide-react'

export function SignalFeed() {
  const { signalsHistory } = useTradingStore()

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Radio className="w-4 h-4 text-blue-400" />
          <span className="text-gray-200 font-mono">SIGNALS</span>
          <div className="ml-auto flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
            <span className="text-[9px] text-gray-500 font-mono">LIVE</span>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-0">
        <ScrollArea className="h-[180px]">
          {signalsHistory && signalsHistory.length > 0 ? (
            <div className="space-y-1">
              {signalsHistory.slice(0, 20).map((sig, idx) => {
                const isLong = sig.direction === 'LONG'
                const confPct = (sig.confidence * 100).toFixed(0)
                const evScore = sig.ev_score?.toFixed(2) || '--'

                return (
                  <div
                    key={idx}
                    className={`flex items-center gap-2 py-1.5 px-2 rounded text-[10px] font-mono ${
                      idx === 0 ? 'bg-[#121a26] border border-[#1e2a3d]' : ''
                    }`}
                  >
                    {isLong ? (
                      <ArrowUp className="w-3 h-3 text-emerald-400 shrink-0" />
                    ) : (
                      <ArrowDown className="w-3 h-3 text-red-400 shrink-0" />
                    )}
                    <Badge
                      variant="outline"
                      className={`text-[8px] px-1 py-0 ${
                        isLong
                          ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                          : 'bg-red-500/20 text-red-400 border-red-500/30'
                      }`}
                    >
                      {sig.direction}
                    </Badge>
                    <span className="text-gray-400">{sig.symbol?.split('/')[0]}</span>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="text-gray-500">C:{confPct}%</span>
                      <span className="text-blue-400">EV:{evScore}</span>
                    </div>
                    {idx === 0 && <Zap className="w-3 h-3 text-amber-400 shrink-0" />}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center py-6">
              <Radio className="w-6 h-6 text-gray-700 mx-auto mb-2" />
              <div className="text-[10px] text-gray-500 font-mono">Waiting for signals...</div>
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
