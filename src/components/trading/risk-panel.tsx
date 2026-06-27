/**
 * RiskPanel — Circuit breakers, risk controls, and safety status.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Shield, ShieldAlert, ShieldCheck, ShieldX, AlertTriangle } from 'lucide-react'

export function RiskPanel() {
  const {
    circuitBreakers,
    isTradingAllowed,
    killSwitchActive,
    maxDrawdownPct,
    dailyLossPct,
    leverage,
    exposurePct,
  } = useTradingStore()

  const breakers = [
    { label: 'Max Drawdown', active: circuitBreakers?.max_drawdown || false, value: `${maxDrawdownPct.toFixed(1)}%` },
    { label: 'Daily Loss', active: circuitBreakers?.daily_loss || false, value: `${dailyLossPct.toFixed(1)}%` },
    { label: 'Volatility', active: circuitBreakers?.volatility || false, value: '--' },
  ]

  return (
    <Card className="bg-[#0d1117] border-[#1e2a3d] h-full">
      <CardHeader className="pb-2 px-3 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Shield className="w-4 h-4 text-amber-400" />
          <span className="text-gray-200 font-mono">RISK</span>
          {!isTradingAllowed && (
            <ShieldX className="w-4 h-4 text-red-400 ml-auto" />
          )}
          {isTradingAllowed && !killSwitchActive && (
            <ShieldCheck className="w-4 h-4 text-emerald-400 ml-auto" />
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3 space-y-2">
        {/* Trading Status */}
        <div className={`text-center py-2 rounded ${
          killSwitchActive
            ? 'bg-red-500/20 border border-red-500/30'
            : !isTradingAllowed
            ? 'bg-yellow-500/10 border border-yellow-500/20'
            : 'bg-emerald-500/10 border border-emerald-500/20'
        }`}>
          {killSwitchActive ? (
            <>
              <ShieldX className="w-5 h-5 text-red-400 mx-auto mb-1" />
              <div className="text-xs text-red-400 font-mono font-bold">KILL SWITCH ACTIVE</div>
            </>
          ) : !isTradingAllowed ? (
            <>
              <ShieldAlert className="w-5 h-5 text-yellow-400 mx-auto mb-1" />
              <div className="text-xs text-yellow-400 font-mono font-bold">TRADING BLOCKED</div>
            </>
          ) : (
            <>
              <ShieldCheck className="w-5 h-5 text-emerald-400 mx-auto mb-1" />
              <div className="text-xs text-emerald-400 font-mono font-bold">TRADING OK</div>
            </>
          )}
        </div>

        {/* Circuit Breakers */}
        <div>
          <div className="text-[10px] text-gray-500 font-mono mb-1">CIRCUIT BREAKERS</div>
          <div className="space-y-1">
            {breakers.map((breaker) => (
              <div
                key={breaker.label}
                className={`flex items-center justify-between py-1 px-2 rounded text-[10px] font-mono ${
                  breaker.active ? 'bg-red-500/10' : 'bg-[#121a26]'
                }`}
              >
                <div className="flex items-center gap-1">
                  {breaker.active ? (
                    <AlertTriangle className="w-3 h-3 text-red-400" />
                  ) : (
                    <ShieldCheck className="w-3 h-3 text-emerald-600" />
                  )}
                  <span className={breaker.active ? 'text-red-400' : 'text-gray-400'}>{breaker.label}</span>
                </div>
                <span className={breaker.active ? 'text-red-400' : 'text-gray-500'}>{breaker.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Leverage & Exposure */}
        <div className="grid grid-cols-2 gap-2">
          <div className="text-center bg-[#121a26] rounded p-2">
            <div className="text-lg font-bold text-white font-mono">{leverage}x</div>
            <div className="text-[9px] text-gray-500 font-mono">LEVERAGE</div>
          </div>
          <div className="text-center bg-[#121a26] rounded p-2">
            <div className="text-lg font-bold text-white font-mono">{exposurePct.toFixed(0)}%</div>
            <div className="text-[9px] text-gray-500 font-mono">EXPOSURE</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
