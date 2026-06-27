/**
 * BrainChart — ObservableHQ-style visualization for the PPMT Brain.
 *
 * Shows:
 *  1. Entropy timeline (area chart)
 *  2. Regime transitions (step chart)
 *  3. Pattern match quality (bar chart)
 *  4. SAX pattern stream visualization
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, BarChart, Bar,
  ComposedChart, Line, ReferenceLine,
} from 'recharts'
import { Brain, Waves, GitBranch, Activity } from 'lucide-react'

const SAX_COLORS: Record<string, string> = {
  a: '#ef4444', b: '#f97316', c: '#eab308', d: '#22c55e', e: '#10b981',
  f: '#14b8a6',
}

const REGIME_COLORS: Record<string, string> = {
  trending_up: '#10b981', trending_down: '#ef4444', ranging: '#eab308', volatile: '#f97316',
}

export function BrainChart() {
  const {
    entropyHistory, regimeHistory, patternMatchHistory,
    patternBuffer, entropy, regime, livingTrieStats,
    latestSignal,
  } = useTradingStore()

  // Format time axis
  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`
  }

  return (
    <div className="space-y-3">
      {/* ─── Entropy Timeline ──────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Waves className="w-3.5 h-3.5 text-cyan-400" />
            <span className="text-gray-300 font-mono">ENTROPY TIMELINE</span>
            <Badge variant="outline" className="text-[9px] font-mono text-cyan-400 border-cyan-500/30 ml-auto px-1.5 py-0">
              {entropy.toFixed(3)}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[120px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={entropyHistory} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="entropyGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={[0, 1]} tick={{ fontSize: 9, fill: '#5a6878' }} width={30} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number) => [val.toFixed(3), 'Entropy']}
                />
                <ReferenceLine y={0.7} stroke="#ef4444" strokeDasharray="3 3" strokeOpacity={0.5} />
                <ReferenceLine y={0.4} stroke="#10b981" strokeDasharray="3 3" strokeOpacity={0.5} />
                <Area type="monotone" dataKey="value" stroke="#06b6d4" fill="url(#entropyGrad)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="flex justify-between text-[9px] text-gray-600 font-mono mt-1 px-1">
            <span>LOW (predictable)</span>
            <span>HIGH (chaotic)</span>
          </div>
        </CardContent>
      </Card>

      {/* ─── Regime Transitions ──────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Activity className="w-3.5 h-3.5 text-purple-400" />
            <span className="text-gray-300 font-mono">REGIME DETECTION</span>
            <Badge
              variant="outline"
              className="text-[9px] font-mono ml-auto px-1.5 py-0"
              style={{
                color: REGIME_COLORS[regime] || '#8a9bb0',
                borderColor: `${REGIME_COLORS[regime] || '#1e2a3d'}40`,
                backgroundColor: `${REGIME_COLORS[regime] || 'transparent'}20`,
              }}
            >
              {(regime || 'UNKNOWN').toUpperCase().replace('_', ' ')}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[80px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={regimeHistory} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={[-0.5, 3.5]} ticks={[0, 1, 2, 3]} tickFormatter={(v: number) => ['VOL', 'RNG', 'DWN', 'UPP'][v] || ''} tick={{ fontSize: 8, fill: '#5a6878' }} width={30} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(v: number) => [['Volatile', 'Ranging', 'Trending ↓', 'Trending ↑'][v] || '?', 'Regime']}
                />
                <Line type="stepAfter" dataKey="regimeNum" stroke="#a78bfa" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* ─── Pattern Match Quality ──────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <GitBranch className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-300 font-mono">PATTERN MATCH QUALITY</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[80px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={patternMatchHistory.slice(-40)} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={[0, 1]} tick={{ fontSize: 9, fill: '#5a6878' }} width={30} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number, name: string) => [val.toFixed(3), name === 'matchScore' ? 'Match Score' : 'Path Length']}
                />
                <Bar dataKey="matchScore" fill="#10b981" radius={[2, 2, 0, 0]} opacity={0.8} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* ─── SAX Pattern Stream ──────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Brain className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-gray-300 font-mono">SAX PATTERN STREAM</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-2">
          <div className="flex gap-1.5 flex-wrap items-end">
            {patternBuffer.length > 0 ? (
              patternBuffer.map((sym, i) => {
                const isLast = i === patternBuffer.length - 1
                return (
                  <div key={i} className="flex flex-col items-center">
                    <div
                      className={`w-8 h-8 rounded flex items-center justify-center text-xs font-mono font-bold border transition-all duration-300 ${
                        isLast ? 'scale-125 ring-2 ring-blue-400/60' : ''
                      }`}
                      style={{
                        backgroundColor: `${SAX_COLORS[sym] || '#6b7280'}30`,
                        color: SAX_COLORS[sym] || '#9ca3af',
                        borderColor: `${SAX_COLORS[sym] || '#6b7280'}50`,
                      }}
                    >
                      {sym}
                    </div>
                    {i < patternBuffer.length - 1 && (
                      <div className="w-4 h-0.5 bg-gray-700 mt-1" />
                    )}
                  </div>
                )
              })
            ) : (
              <span className="text-[10px] text-gray-600 font-mono">waiting for data...</span>
            )}
          </div>
          {latestSignal && (
            <div className="mt-2 flex items-center gap-2 text-[9px] font-mono">
              <span className="text-gray-500">Latest path:</span>
              <span className="text-blue-400">{latestSignal.pattern_path}</span>
              <span className="text-gray-600">|</span>
              <span className={latestSignal.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}>
                {latestSignal.direction} {(latestSignal.confidence * 100).toFixed(0)}%
              </span>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
