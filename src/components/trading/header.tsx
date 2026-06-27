/**
 * StatusHeader — Top bar with connection status, mode indicator, and controls.
 */
'use client'

import { useTradingStore } from '@/stores/trading-store'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Power,
  PowerOff,
  Skull,
  Wifi,
  WifiOff,
  Zap,
  Activity,
} from 'lucide-react'

interface StatusHeaderProps {
  onStartStop: () => void
  onKillSwitch: () => void
  onToggleAuto: (enabled: boolean) => void
}

export function StatusHeader({ onStartStop, onKillSwitch, onToggleAuto }: StatusHeaderProps) {
  const {
    isConnected,
    engineMode,
    isRunning,
    autoMode,
    symbol,
    timeframe,
    currentPrice,
    killSwitchActive,
  } = useTradingStore()

  const modeColor = engineMode === 'live'
    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
    : engineMode === 'demo'
    ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
    : 'bg-red-500/20 text-red-400 border-red-500/30'

  const modeLabel = engineMode === 'live' ? 'LIVE' : engineMode === 'demo' ? 'DEMO' : 'OFFLINE'

  return (
    <header className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e2a3d]">
      {/* Left: Logo + Status */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5 text-blue-400" />
          <span className="font-bold text-white text-lg tracking-tight">PPMT</span>
          <span className="text-[10px] text-gray-500 font-mono">TERMINAL</span>
        </div>

        <div className="h-4 w-px bg-[#1e2a3d]" />

        <Badge variant="outline" className={`${modeColor} text-[10px] font-mono px-2 py-0.5`}>
          {isConnected ? <Wifi className="w-3 h-3 mr-1" /> : <WifiOff className="w-3 h-3 mr-1" />}
          {modeLabel}
        </Badge>

        {isRunning && (
          <div className="flex items-center gap-1">
            <Activity className="w-3 h-3 text-emerald-400 animate-pulse" />
            <span className="text-[10px] text-emerald-400 font-mono">RUNNING</span>
          </div>
        )}
      </div>

      {/* Center: Market Info */}
      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className="text-xs text-gray-400 font-mono">{symbol}</div>
          <div className="text-lg font-bold text-white font-mono">
            ${currentPrice > 0 ? currentPrice.toFixed(2) : '---.--'}
          </div>
        </div>
        <Badge variant="outline" className="text-[10px] font-mono text-gray-400 border-gray-600">
          {timeframe}
        </Badge>
      </div>

      {/* Right: Controls */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch
            id="auto-mode"
            checked={autoMode}
            onCheckedChange={onToggleAuto}
            className="data-[state=checked]:bg-emerald-600 data-[state=unchecked]:bg-gray-700"
          />
          <Label htmlFor="auto-mode" className="text-[10px] text-gray-400 font-mono">
            AUTO
          </Label>
        </div>

        <Button
          size="sm"
          variant={isRunning ? 'destructive' : 'default'}
          onClick={onStartStop}
          className="h-7 text-xs font-mono gap-1"
          disabled={!isConnected}
        >
          {isRunning ? (
            <>
              <PowerOff className="w-3 h-3" />
              STOP
            </>
          ) : (
            <>
              <Power className="w-3 h-3" />
              START
            </>
          )}
        </Button>

        <Button
          size="sm"
          variant="destructive"
          onClick={onKillSwitch}
          className="h-7 text-xs font-mono gap-1 bg-red-900 hover:bg-red-800"
          disabled={killSwitchActive || !isConnected}
        >
          <Skull className="w-3 h-3" />
          KILL
        </Button>
      </div>
    </header>
  )
}
