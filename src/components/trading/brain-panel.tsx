/**
 * BrainPanel — PPMT pattern analysis: SAX buffer, regime, entropy, trie stats.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Brain, Waves, GitBranch, Activity } from 'lucide-react'

const SAX_COLORS: Record<string, string> = {
  // SAX alphabet (legacy a-f)
  a: 'bg-red-500/30 text-red-300 border-red-500/40',
  b: 'bg-orange-500/30 text-orange-300 border-orange-500/40',
  c: 'bg-yellow-500/30 text-yellow-300 border-yellow-500/40',
  d: 'bg-green-500/30 text-green-300 border-green-500/40',
  e: 'bg-emerald-500/30 text-emerald-300 border-emerald-500/40',
  f: 'bg-teal-500/30 text-teal-300 border-teal-500/40',
  // Paper engine U/D/F encoding (Up / Down / Flat)
  U: 'bg-emerald-500/30 text-emerald-300 border-emerald-500/40',
  D: 'bg-red-500/30 text-red-300 border-red-500/40',
  F: 'bg-gray-500/30 text-gray-300 border-gray-500/40',
}

const REGIME_LABELS: Record<string, { label: string; color: string }> = {
  trending_up: { label: 'TREND UP', color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' },
  trending_down: { label: 'TREND DOWN', color: 'bg-red-500/20 text-red-400 border-red-500/30' },
  ranging: { label: 'RANGING', color: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' },
  volatile: { label: 'VOLATILE', color: 'bg-orange-500/20 text-orange-400 border-orange-500/30' },
}

export function BrainPanel() {
  const {
    patternBuffer,
    entropy,
    regime,
    livingTrieStats,
    candlesProcessed,
    latestSignal,
  } = useTradingStore()

  const regimeInfo = REGIME_LABELS[regime] || { label: regime.toUpperCase(), color: 'bg-gray-500/20 text-gray-400 border-gray-500/30' }

  // Entropy gauge
  const entropyPct = Math.min(100, Math.max(0, entropy * 100))
  const entropyColor = entropy > 0.7 ? 'bg-red-500' : entropy > 0.4 ? 'bg-yellow-500' : 'bg-emerald-500'

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Brain className="w-4 h-4 text-blue-400" />
          <span className="text-gray-200 font-mono">PPMT BRAIN</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3 space-y-3">
        {/* Regime */}
        <div>
          <div className="text-[10px] text-gray-500 font-mono mb-1">REGIME</div>
          <Badge variant="outline" className={`${regimeInfo.color} text-[10px] font-mono px-2 py-0.5`}>
            {regimeInfo.label}
          </Badge>
        </div>

        {/* SAX Pattern Buffer */}
        <div>
          <div className="text-[10px] text-gray-500 font-mono mb-1">PATTERN BUFFER</div>
          <div className="flex gap-1 flex-wrap">
            {patternBuffer.length > 0 ? (
              patternBuffer.map((sym, i) => (
                <span
                  key={i}
                  className={`inline-flex items-center justify-center w-6 h-6 rounded text-xs font-mono font-bold border ${
                    SAX_COLORS[sym] || 'bg-gray-500/30 text-gray-300 border-gray-500/40'
                  } ${i === patternBuffer.length - 1 ? 'ring-1 ring-blue-400/50' : ''}`}
                >
                  {sym}
                </span>
              ))
            ) : (
              <span className="text-xs text-gray-600 font-mono">waiting...</span>
            )}
          </div>
        </div>

        {/* Entropy */}
        <div>
          <div className="flex justify-between mb-1">
            <span className="text-[10px] text-gray-500 font-mono">ENTROPY</span>
            <span className="text-[10px] text-gray-400 font-mono">{entropy.toFixed(3)}</span>
          </div>
          <div className="h-1.5 bg-[#1a2334] rounded-full overflow-hidden">
            <div
              className={`h-full ${entropyColor} rounded-full transition-all duration-500`}
              style={{ width: `${entropyPct}%` }}
            />
          </div>
        </div>

        {/* Latest Signal */}
        {latestSignal && (
          <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
            <div className="text-[10px] text-gray-500 font-mono mb-1">LATEST SIGNAL</div>
            <div className="flex items-center gap-2">
              <Badge
                variant="outline"
                className={`text-[10px] font-mono ${
                  latestSignal.direction === 'LONG'
                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    : 'bg-red-500/20 text-red-400 border-red-500/30'
                }`}
              >
                {latestSignal.direction}
              </Badge>
              <span className="text-[10px] text-gray-400 font-mono">
                Conf: {(latestSignal.confidence * 100).toFixed(0)}%
              </span>
              <span className="text-[10px] text-gray-400 font-mono">
                EV: {latestSignal.ev_score?.toFixed(2) || '--'}
              </span>
            </div>
            <div className="text-[10px] text-gray-500 font-mono mt-1">
              Path: {latestSignal.pattern_path || '--'}
            </div>
          </div>
        )}

        {/* Living Trie Stats */}
        {livingTrieStats && (
          <div>
            <div className="text-[10px] text-gray-500 font-mono mb-1 flex items-center gap-1">
              <GitBranch className="w-3 h-3" />
              LIVING TRIE
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className="text-sm font-bold text-white font-mono">{livingTrieStats.pattern_count?.toLocaleString()}</div>
                <div className="text-[9px] text-gray-500 font-mono">PATTERNS</div>
              </div>
              <div>
                <div className="text-sm font-bold text-white font-mono">{livingTrieStats.max_depth}</div>
                <div className="text-[9px] text-gray-500 font-mono">DEPTH</div>
              </div>
              <div>
                <div className="text-sm font-bold text-white font-mono">{livingTrieStats.trading_observations?.toLocaleString()}</div>
                <div className="text-[9px] text-gray-500 font-mono">OBS</div>
              </div>
            </div>
          </div>
        )}

        {/* Candles Processed */}
        <div className="flex items-center gap-1">
          <Activity className="w-3 h-3 text-gray-500" />
          <span className="text-[10px] text-gray-500 font-mono">
            {candlesProcessed.toLocaleString()} candles processed
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
