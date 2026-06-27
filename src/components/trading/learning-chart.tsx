/**
 * LearningChart — ObservableHQ-style visualization for PPMT Learning process.
 *
 * Shows:
 *  1. Win Rate evolution over time (with learning stage bands)
 *  2. Confidence & EV Score scatter/timeline
 *  3. Learning stage progression
 *  4. Drift detection & model health
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ScatterChart, Scatter,
  ReferenceArea, ReferenceLine, Cell, BarChart, Bar,
} from 'recharts'
import { GraduationCap, TrendingUp, Target, AlertTriangle, Zap, RefreshCw } from 'lucide-react'

const STAGE_CONFIG: Record<string, { color: string; label: string; description: string; progress: number }> = {
  BOOTSTRAP: { color: '#6b7280', label: 'BOOTSTRAP', description: 'Collecting initial data', progress: 10 },
  LEARNING: { color: '#3b82f6', label: 'LEARNING', description: 'Building pattern database', progress: 35 },
  ADAPTING: { color: '#8b5cf6', label: 'ADAPTING', description: 'Adjusting to market conditions', progress: 60 },
  OPTIMIZED: { color: '#10b981', label: 'OPTIMIZED', description: 'Pattern matching refined', progress: 80 },
  MATURE: { color: '#f59e0b', label: 'MATURE', description: 'Full pattern recognition', progress: 95 },
}

export function LearningChart() {
  const {
    winRateHistory, confidenceHistory, learningStageHistory,
    learningStage, winRate, totalTrades, winningTrades,
    driftDetected, lastRetrainTime,
  } = useTradingStore()

  const stageConfig = STAGE_CONFIG[learningStage] || STAGE_CONFIG.BOOTSTRAP
  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return `${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`
  }

  // Compute profit factor from winRate
  const profitFactor = winRate > 0 && winRate < 1
    ? (winRate * 2.5) / (1 - winRate)
    : winRate >= 1 ? 10 : 0

  return (
    <div className="space-y-3">
      {/* ─── Learning Stage Dashboard ───────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <GraduationCap className="w-3.5 h-3.5 text-purple-400" />
            <span className="text-gray-300 font-mono">LEARNING STAGE</span>
            {driftDetected && (
              <Badge variant="outline" className="text-[8px] font-mono bg-orange-500/20 text-orange-400 border-orange-500/30 ml-auto px-1.5 py-0">
                <AlertTriangle className="w-2.5 h-2.5 mr-0.5" />DRIFT
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-2 space-y-2">
          {/* Stage Progress Bar */}
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <div className="flex justify-between text-[9px] font-mono mb-1">
                <span style={{ color: stageConfig.color }}>{stageConfig.label}</span>
                <span className="text-gray-500">{stageConfig.description}</span>
              </div>
              <div className="h-2 bg-[#1a2334] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-1000"
                  style={{
                    width: `${stageConfig.progress}%`,
                    backgroundColor: stageConfig.color,
                  }}
                />
              </div>
              {/* Stage markers */}
              <div className="flex justify-between text-[7px] text-gray-600 font-mono mt-0.5">
                <span>BOOT</span>
                <span>LEARN</span>
                <span>ADAPT</span>
                <span>OPT</span>
                <span>MATURE</span>
              </div>
            </div>
          </div>

          {/* Key metrics */}
          <div className="grid grid-cols-3 gap-2">
            <div className="text-center bg-[#121a26] rounded p-1.5">
              <div className="text-sm font-bold font-mono" style={{ color: winRate > 0.55 ? '#10b981' : winRate > 0 ? '#eab308' : '#6b7280' }}>
                {winRate > 0 ? (winRate * 100).toFixed(1) + '%' : '--'}
              </div>
              <div className="text-[8px] text-gray-500 font-mono">WIN RATE</div>
            </div>
            <div className="text-center bg-[#121a26] rounded p-1.5">
              <div className="text-sm font-bold text-white font-mono">{profitFactor.toFixed(2)}</div>
              <div className="text-[8px] text-gray-500 font-mono">PFACTOR</div>
            </div>
            <div className="text-center bg-[#121a26] rounded p-1.5">
              <div className="text-sm font-bold text-white font-mono">{totalTrades}</div>
              <div className="text-[8px] text-gray-500 font-mono">TRADES</div>
            </div>
          </div>

          {lastRetrainTime && (
            <div className="flex items-center gap-1 text-[9px] text-gray-500 font-mono">
              <RefreshCw className="w-2.5 h-2.5" />
              Last retrain: {new Date(lastRetrainTime * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ─── Win Rate Evolution ──────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-300 font-mono">WIN RATE EVOLUTION</span>
            <Badge variant="outline" className="text-[9px] font-mono ml-auto px-1.5 py-0"
              style={{
                color: winRate > 0.6 ? '#10b981' : winRate > 0.5 ? '#eab308' : '#6b7280',
                borderColor: winRate > 0.6 ? '#10b98140' : winRate > 0.5 ? '#eab30840' : '#6b728040',
              }}
            >
              {winRate > 0 ? (winRate * 100).toFixed(1) + '%' : 'N/A'}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[130px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={winRateHistory} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="wrGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis domain={[0, 1]} tick={{ fontSize: 9, fill: '#5a6878' }} width={30} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number, name: string) => [
                    name === 'winRate' ? `${(val * 100).toFixed(1)}%` : val,
                    name === 'winRate' ? 'Win Rate' : name,
                  ]}
                />
                {/* Target line at 55% */}
                <ReferenceLine y={0.55} stroke="#eab308" strokeDasharray="4 4" strokeOpacity={0.6} label={{ value: '55% target', position: 'right', fill: '#eab308', fontSize: 8 }} />
                <ReferenceLine y={0.65} stroke="#10b981" strokeDasharray="4 4" strokeOpacity={0.4} label={{ value: '65%', position: 'right', fill: '#10b981', fontSize: 8 }} />
                <Area type="monotone" dataKey="winRate" fill="url(#wrGrad)" stroke="none" />
                <Line type="monotone" dataKey="winRate" stroke="#10b981" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* ─── Confidence & EV Score ───────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Target className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-gray-300 font-mono">SIGNAL CONFIDENCE & EV</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[100px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={confidenceHistory} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis yAxisId="left" domain={[0, 1]} tick={{ fontSize: 9, fill: '#5a6878' }} width={30} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 2]} tick={{ fontSize: 9, fill: '#5a6878' }} width={25} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                />
                <ReferenceLine yAxisId="left" y={0.6} stroke="#3b82f6" strokeDasharray="3 3" strokeOpacity={0.4} />
                <Line yAxisId="left" type="monotone" dataKey="confidence" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
                <Line yAxisId="right" type="monotone" dataKey="evScore" stroke="#f59e0b" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="flex gap-3 text-[9px] font-mono mt-1 px-1">
            <div className="flex items-center gap-1">
              <div className="w-3 h-0.5 bg-blue-500" />
              <span className="text-gray-500">Confidence</span>
            </div>
            <div className="flex items-center gap-1">
              <div className="w-3 h-0.5 bg-amber-500" style={{ borderTop: '1px dashed #f59e0b' }} />
              <span className="text-gray-500">EV Score</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ─── Learning Stage Timeline ─────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-gray-300 font-mono">LEARNING PROGRESSION</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <div className="h-[80px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={learningStageHistory} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3d" />
                <XAxis dataKey="time" tickFormatter={formatTime} tick={{ fontSize: 9, fill: '#5a6878' }} interval="preserveStartEnd" />
                <YAxis yAxisId="left" domain={[0, 4]} ticks={[0, 1, 2, 3, 4]} tickFormatter={(v: number) => ['BOOT', 'LEARN', 'ADAPT', 'OPT', 'MAT'][v]} tick={{ fontSize: 7, fill: '#5a6878' }} width={35} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 3]} tick={{ fontSize: 9, fill: '#5a6878' }} width={25} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#121a26', border: '1px solid #1e2a3d', fontSize: 10, fontFamily: 'monospace' }}
                  labelFormatter={formatTime}
                  formatter={(val: number, name: string) => {
                    if (name === 'stageNum') return [STAGE_CONFIG[['BOOTSTRAP', 'LEARNING', 'ADAPTING', 'OPTIMIZED', 'MATURE'][val] || ''].label, 'Stage']
                    if (name === 'pf') return [val.toFixed(2), 'PF']
                    return [val, name]
                  }}
                />
                <Bar yAxisId="right" dataKey="pf" fill="#8b5cf630" stroke="#8b5cf6" strokeWidth={1} radius={[2, 2, 0, 0]} />
                <Line type="stepAfter" yAxisId="left" dataKey="stageNum" stroke="#f59e0b" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
