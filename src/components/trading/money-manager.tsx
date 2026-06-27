/**
 * MoneyManager — Premium risk & position sizing management panel.
 * Intuitive controls for risk per trade, position sizing, Kelly criterion,
 * drawdown limits, trailing stops, and break-even management.
 * High-style UI/UX with smooth interactions.
 */
'use client'

import { useTradingStore, MoneyManagerSettings } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import {
  Shield, Target, Calculator, Gauge, AlertTriangle,
  TrendingUp, Lock, Unlock, Zap, ChevronDown, Info,
} from 'lucide-react'
import { motion } from 'framer-motion'
import { useTradingSocket } from '@/lib/use-trading-socket'
import { useState, useEffect } from 'react'

function RiskGauge({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = Math.min((value / max) * 100, 100)
  const risk = pct < 33 ? 'LOW' : pct < 66 ? 'MED' : 'HIGH'
  const riskColor = pct < 33 ? '#10b981' : pct < 66 ? '#eab308' : '#ef4444'

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-[#1a2334] rounded-full overflow-hidden">
        <motion.div
          className="h-full rounded-full"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
          style={{ backgroundColor: riskColor }}
        />
      </div>
      <span className="text-[9px] font-mono font-bold min-w-[28px] text-right" style={{ color: riskColor }}>
        {risk}
      </span>
    </div>
  )
}

function MetricCard({ label, value, subtext, color }: { label: string; value: string; subtext?: string; color?: string }) {
  return (
    <div className="bg-[#121a26] rounded-lg p-2.5">
      <div className="text-[8px] text-gray-500 font-mono uppercase tracking-wider">{label}</div>
      <div className="text-sm font-bold font-mono mt-0.5" style={{ color: color || 'white' }}>
        {value}
      </div>
      {subtext && <div className="text-[8px] text-gray-500 font-mono mt-0.5">{subtext}</div>}
    </div>
  )
}

export function MoneyManager() {
  const {
    moneyManager, kellyPercent, suggestedPositionSize,
    riskRewardRatio, portfolioValue, winRate, totalTrades,
    maxDrawdown, maxDrawdownPct, dailyLossPct,
    circuitBreakers, isTradingAllowed, leverage,
    positions, unrealizedPnl, realizedPnl,
  } = useTradingStore()
  const { emit } = useTradingSocket()

  const [expandedSection, setExpandedSection] = useState<string>('risk')

  const mm = moneyManager

  // Optimistic local state for the position-sizing Select — prevents the
  // 'loop back' flicker where Radix Select briefly shows the old value
  // between user click and the next 1.5s engine snapshot.
  const [sizingMethodLocal, setSizingMethodLocal] = useState<string>(
    mm.positionSizingMethod
  )
  // Sync from store when the store value changes (e.g. on engine reset)
  useEffect(() => {
    setSizingMethodLocal(mm.positionSizingMethod)
  }, [mm.positionSizingMethod])

  const updateMM = (updates: Partial<MoneyManagerSettings>) => {
    emit('update-money-manager', updates)
  }

  // Computed values
  const currentRisk = mm.riskPerTradePct * leverage
  const riskLevel = currentRisk < 6 ? 'LOW' : currentRisk < 15 ? 'MEDIUM' : 'HIGH'
  const riskColor = currentRisk < 6 ? '#10b981' : currentRisk < 15 ? '#eab308' : '#ef4444'

  // Kelly info
  const kellyFull = kellyPercent * 100
  const kellyUsed = kellyPercent * mm.kellyFraction * 100
  const isKellySane = kellyUsed < 10

  // Drawdown status
  const drawdownPct = maxDrawdownPct
  const drawdownLevel = drawdownPct < mm.maxDrawdownPct * 0.5 ? 'SAFE' : drawdownPct < mm.maxDrawdownPct * 0.8 ? 'WARNING' : 'CRITICAL'
  const drawdownColor = drawdownLevel === 'SAFE' ? '#10b981' : drawdownLevel === 'WARNING' ? '#eab308' : '#ef4444'

  // Position sizing
  const effectivePosition = mm.positionSizingMethod === 'kelly'
    ? suggestedPositionSize
    : mm.positionSizingMethod === 'risk_parity'
    ? (portfolioValue * mm.riskPerTradePct / 100) * riskRewardRatio
    : portfolioValue * mm.riskPerTradePct / 100

  const openPositions = positions?.length || 0
  const canOpenMore = openPositions < mm.maxConcurrentPositions

  return (
    <div className="space-y-3">
      {/* ─── Risk Overview Card ──────────────────────────────── */}
      <Card className="bg-gradient-to-br from-[#0d1117] to-[#111827] border-[#1e2a3d] overflow-hidden relative">
        <div className={`absolute top-0 left-0 w-full h-0.5 ${isTradingAllowed ? 'bg-gradient-to-r from-emerald-500 via-blue-500 to-purple-500' : 'bg-red-500'}`} />
        <CardHeader className="pb-1 px-4 pt-3">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Shield className="w-4 h-4 text-blue-400" />
            <span className="text-gray-300 font-mono">RISK OVERVIEW</span>
            <Badge
              variant="outline"
              className={`text-[8px] font-mono ml-auto px-2 py-0.5 ${
                isTradingAllowed
                  ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10'
                  : 'text-red-400 border-red-500/30 bg-red-500/10'
              }`}
            >
              {isTradingAllowed ? 'TRADING OK' : 'BLOCKED'}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-3 space-y-3">
          {/* Risk Level Gauge */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">RISK LEVEL</span>
              <span className="text-[9px] font-mono font-bold" style={{ color: riskColor }}>
                {riskLevel} ({currentRisk.toFixed(1)}% effective)
              </span>
            </div>
            <RiskGauge value={currentRisk} max={30} color={riskColor} />
          </div>

          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-2">
            <MetricCard
              label="Kelly %"
              value={`${kellyFull.toFixed(1)}%`}
              subtext={`Using ${(mm.kellyFraction * 100).toFixed(0)}% = ${kellyUsed.toFixed(1)}%`}
              color={isKellySane ? '#10b981' : '#ef4444'}
            />
            <MetricCard
              label="Drawdown"
              value={`${drawdownPct.toFixed(1)}%`}
              subtext={`Limit: ${mm.maxDrawdownPct}%`}
              color={drawdownColor}
            />
            <MetricCard
              label="Positions"
              value={`${openPositions}/${mm.maxConcurrentPositions}`}
              subtext={canOpenMore ? 'Can open more' : 'Max reached'}
              color={canOpenMore ? '#10b981' : '#eab308'}
            />
          </div>

          {/* Circuit Breakers */}
          <div>
            <div className="text-[9px] text-gray-500 font-mono mb-1.5">CIRCUIT BREAKERS</div>
            <div className="flex gap-2">
              {Object.entries(circuitBreakers || {}).map(([key, active]) => (
                <div
                  key={key}
                  className={`flex items-center gap-1 px-2 py-1 rounded text-[8px] font-mono ${
                    active
                      ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                      : 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                  }`}
                >
                  {active ? <AlertTriangle className="w-2.5 h-2.5" /> : <Shield className="w-2.5 h-2.5" />}
                  {key.replace('_', ' ').toUpperCase()}
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ─── Position Sizing ─────────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader
          className="pb-1 px-3 pt-2 cursor-pointer"
          onClick={() => setExpandedSection(expandedSection === 'sizing' ? '' : 'sizing')}
        >
          <CardTitle className="flex items-center gap-2 text-xs">
            <Calculator className="w-3.5 h-3.5 text-cyan-400" />
            <span className="text-gray-300 font-mono">POSITION SIZING</span>
            <ChevronDown className={`w-3 h-3 text-gray-500 ml-auto transition-transform ${expandedSection === 'sizing' ? 'rotate-180' : ''}`} />
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 space-y-3">
          {/* Sizing Method */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">METHOD</span>
            </div>
            <Select
              value={sizingMethodLocal}
              onValueChange={(val) => {
                setSizingMethodLocal(val as MoneyManagerSettings['positionSizingMethod'])
                updateMM({ positionSizingMethod: val as MoneyManagerSettings['positionSizingMethod'] })
              }}
            >
              <SelectTrigger
                id="mm-sizing-method"
                name="positionSizingMethod"
                aria-label="Position sizing method"
                className="h-7 bg-[#121a26] border-[#1e2a3d] text-[10px] font-mono"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#121a26] border-[#1e2a3d]">
                <SelectItem value="fixed" className="text-[10px] font-mono">Fixed % Risk</SelectItem>
                <SelectItem value="kelly" className="text-[10px] font-mono">Kelly Criterion</SelectItem>
                <SelectItem value="risk_parity" className="text-[10px] font-mono">Risk Parity</SelectItem>
                <SelectItem value="volatility_adj" className="text-[10px] font-mono">Volatility Adjusted</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Risk Per Trade */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">RISK PER TRADE</span>
              <span className="text-[10px] text-white font-mono font-bold">{mm.riskPerTradePct}%</span>
            </div>
            <Slider aria-label="Risk per trade percent"
              value={[mm.riskPerTradePct]}
              min={0.5}
              max={5}
              step={0.5}
              onValueChange={([val]) => updateMM({ riskPerTradePct: val })}
              className="py-1"
            />
            <div className="flex justify-between text-[7px] text-gray-600 font-mono">
              <span>Conservative</span>
              <span>Aggressive</span>
            </div>
          </div>

          {/* Kelly Fraction */}
          {mm.positionSizingMethod === 'kelly' && (
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-[9px] text-gray-500 font-mono">KELLY FRACTION</span>
                <span className="text-[10px] text-white font-mono font-bold">{(mm.kellyFraction * 100).toFixed(0)}%</span>
              </div>
              <Slider aria-label="Kelly fraction"
                value={[mm.kellyFraction]}
                min={0.25}
                max={1.0}
                step={0.05}
                onValueChange={([val]) => updateMM({ kellyFraction: val })}
                className="py-1"
              />
              <div className="flex justify-between text-[7px] text-gray-600 font-mono">
                <span>¼ Kelly (safe)</span>
                <span>Full Kelly (risky)</span>
              </div>
            </div>
          )}

          {/* Calculated Position Size */}
          <div className="bg-[#121a26] rounded-lg p-2.5 flex items-center gap-2">
            <Target className="w-4 h-4 text-cyan-400 shrink-0" />
            <div>
              <div className="text-[8px] text-gray-500 font-mono">SUGGESTED SIZE</div>
              <div className="text-sm font-bold text-white font-mono">
                ${effectivePosition.toFixed(2)}
              </div>
            </div>
            <div className="ml-auto text-right">
              <div className="text-[8px] text-gray-500 font-mono">R:R RATIO</div>
              <div className="text-sm font-bold text-emerald-400 font-mono">
                1:{riskRewardRatio.toFixed(1)}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ─── Trade Parameters ────────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader
          className="pb-1 px-3 pt-2 cursor-pointer"
          onClick={() => setExpandedSection(expandedSection === 'params' ? '' : 'params')}
        >
          <CardTitle className="flex items-center gap-2 text-xs">
            <Target className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-gray-300 font-mono">TRADE PARAMETERS</span>
            <ChevronDown className={`w-3 h-3 text-gray-500 ml-auto transition-transform ${expandedSection === 'params' ? 'rotate-180' : ''}`} />
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 space-y-3">
          {/* Take Profit Multiplier */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">TAKE PROFIT (x Risk)</span>
              <span className="text-[10px] text-emerald-400 font-mono font-bold">{mm.takeProfitMultiplier}x</span>
            </div>
            <Slider aria-label="Take profit multiplier"
              value={[mm.takeProfitMultiplier]}
              min={1.0}
              max={5.0}
              step={0.5}
              onValueChange={([val]) => updateMM({ takeProfitMultiplier: val })}
              className="py-1"
            />
            <div className="flex justify-between text-[7px] text-gray-600 font-mono">
              <span>1x (tight)</span>
              <span>5x (wide)</span>
            </div>
          </div>

          {/* Stop Loss ATR */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">STOP LOSS (ATR)</span>
              <span className="text-[10px] text-red-400 font-mono font-bold">{mm.stopLossATR}x</span>
            </div>
            <Slider aria-label="Stop loss ATR multiplier"
              value={[mm.stopLossATR]}
              min={0.5}
              max={3.0}
              step={0.25}
              onValueChange={([val]) => updateMM({ stopLossATR: val })}
              className="py-1"
            />
          </div>

          {/* Leverage */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">DEFAULT LEVERAGE</span>
              <span className="text-[10px] text-amber-400 font-mono font-bold">{mm.defaultLeverage}x</span>
            </div>
            <Slider aria-label="Default leverage"
              value={[mm.defaultLeverage]}
              min={1}
              max={mm.maxLeverage}
              step={1}
              onValueChange={([val]) => updateMM({ defaultLeverage: val })}
              className="py-1"
            />
          </div>

          {/* Max Leverage */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">MAX LEVERAGE</span>
              <span className="text-[10px] text-red-400 font-mono font-bold">{mm.maxLeverage}x</span>
            </div>
            <Slider aria-label="Max leverage"
              value={[mm.maxLeverage]}
              min={1}
              max={20}
              step={1}
              onValueChange={([val]) => updateMM({ maxLeverage: val })}
              className="py-1"
            />
          </div>

          {/* Max Concurrent Positions */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">MAX POSITIONS</span>
              <span className="text-[10px] text-white font-mono font-bold">{mm.maxConcurrentPositions}</span>
            </div>
            <Slider aria-label="Max concurrent positions"
              value={[mm.maxConcurrentPositions]}
              min={1}
              max={10}
              step={1}
              onValueChange={([val]) => updateMM({ maxConcurrentPositions: val })}
              className="py-1"
            />
          </div>
        </CardContent>
      </Card>

      {/* ─── Risk Limits ─────────────────────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader
          className="pb-1 px-3 pt-2 cursor-pointer"
          onClick={() => setExpandedSection(expandedSection === 'limits' ? '' : 'limits')}
        >
          <CardTitle className="flex items-center gap-2 text-xs">
            <Gauge className="w-3.5 h-3.5 text-red-400" />
            <span className="text-gray-300 font-mono">RISK LIMITS</span>
            <ChevronDown className={`w-3 h-3 text-gray-500 ml-auto transition-transform ${expandedSection === 'limits' ? 'rotate-180' : ''}`} />
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 space-y-3">
          {/* Max Drawdown */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">MAX DRAWDOWN</span>
              <span className="text-[10px] text-red-400 font-mono font-bold">{mm.maxDrawdownPct}%</span>
            </div>
            <Slider aria-label="Max drawdown percent"
              value={[mm.maxDrawdownPct]}
              min={5}
              max={30}
              step={5}
              onValueChange={([val]) => updateMM({ maxDrawdownPct: val })}
              className="py-1"
            />
            <div className="flex justify-between text-[7px] text-gray-600 font-mono">
              <span>5% (very safe)</span>
              <span>30% (risky)</span>
            </div>
          </div>

          {/* Daily Loss Limit */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">DAILY LOSS LIMIT</span>
              <span className="text-[10px] text-amber-400 font-mono font-bold">{mm.dailyLossLimitPct}%</span>
            </div>
            <Slider aria-label="Daily loss limit percent"
              value={[mm.dailyLossLimitPct]}
              min={1}
              max={15}
              step={1}
              onValueChange={([val]) => updateMM({ dailyLossLimitPct: val })}
              className="py-1"
            />
          </div>

          {/* Max Correlated */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[9px] text-gray-500 font-mono">MAX CORRELATED POSITIONS</span>
              <span className="text-[10px] text-white font-mono font-bold">{mm.maxCorrelatedPositions}</span>
            </div>
            <Slider aria-label="Max correlated positions"
              value={[mm.maxCorrelatedPositions]}
              min={1}
              max={5}
              step={1}
              onValueChange={([val]) => updateMM({ maxCorrelatedPositions: val })}
              className="py-1"
            />
          </div>
        </CardContent>
      </Card>

      {/* ─── Trailing Stop & Break-Even ──────────────────────── */}
      <Card className="bg-[#0d1117] border-[#1e2a3d]">
        <CardHeader className="pb-1 px-3 pt-2">
          <CardTitle className="flex items-center gap-2 text-xs">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-gray-300 font-mono">EXIT MANAGEMENT</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 space-y-3">
          {/* Trailing Stop */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-300 font-mono">Trailing Stop</span>
              <Info className="w-3 h-3 text-gray-600" />
            </div>
            <Switch aria-label="Trailing stop toggle"
              checked={mm.trailingStopEnabled}
              onCheckedChange={(val) => updateMM({ trailingStopEnabled: val })}
            />
          </div>
          {mm.trailingStopEnabled && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="space-y-2 pl-2"
            >
              <div>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[9px] text-gray-500 font-mono">ACTIVATE AFTER</span>
                  <span className="text-[10px] text-amber-400 font-mono">{mm.trailingStopActivationPct}% profit</span>
                </div>
                <Slider aria-label="Trailing stop activation percent"
                  value={[mm.trailingStopActivationPct]}
                  min={0.3}
                  max={3.0}
                  step={0.1}
                  onValueChange={([val]) => updateMM({ trailingStopActivationPct: val })}
                  className="py-1"
                />
              </div>
              <div>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[9px] text-gray-500 font-mono">TRAIL DISTANCE</span>
                  <span className="text-[10px] text-amber-400 font-mono">{mm.trailingStopDistancePct}%</span>
                </div>
                <Slider aria-label="Trailing stop distance percent"
                  value={[mm.trailingStopDistancePct]}
                  min={0.2}
                  max={2.0}
                  step={0.1}
                  onValueChange={([val]) => updateMM({ trailingStopDistancePct: val })}
                  className="py-1"
                />
              </div>
            </motion.div>
          )}

          <Separator className="bg-[#1e2a3d]" />

          {/* Break-Even */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-300 font-mono">Break-Even Move</span>
              <Info className="w-3 h-3 text-gray-600" />
            </div>
            <Switch
              aria-label="Break-even move toggle"
              checked={mm.breakEvenEnabled}
              onCheckedChange={(val) => updateMM({ breakEvenEnabled: val })}
            />
          </div>
          {mm.breakEvenEnabled && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="pl-2"
            >
              <div className="flex justify-between items-center mb-1">
                <span className="text-[9px] text-gray-500 font-mono">ACTIVATE AFTER</span>
                <span className="text-[10px] text-cyan-400 font-mono">{mm.breakEvenActivationPct}% profit</span>
              </div>
              <Slider aria-label="Break-even activation percent"
                value={[mm.breakEvenActivationPct]}
                min={0.2}
                max={2.0}
                step={0.1}
                onValueChange={([val]) => updateMM({ breakEvenActivationPct: val })}
                className="py-1"
              />
            </motion.div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
