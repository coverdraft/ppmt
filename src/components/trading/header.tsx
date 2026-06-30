/**
 * StatusHeader — Top bar with connection status, mode indicator, and controls.
 * Auto-mode switch uses optimistic local state to prevent flicker
 * caused by race between user click and next snapshot from engine.
 */
'use client'

import { useState, useEffect } from 'react'
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
  ClipboardCopy,
  Download,
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
    // debug export fields (v9 — comprehensive snapshot)
    positions,
    tradeHistory,
    strategies_perf,
    websocketStatus,
    tickCount,
    candlesProcessed,
    lastTickAt,
    moneyManager,
    circuitBreakers,
    activeTokens,
    selectedToken,
    kellyPercent,
    suggestedPositionSize,
    // v9 additions
    exchange,
    patternBuffer,
    entropy,
    regime,
    livingTrieStats,
    signalsHistory,
    latestSignal,
    learningStage,
    learningStageHistory,
    driftDetected,
    lastRetrainTime,
    winRateHistory,
    confidenceHistory,
    patternMatchHistory,
    entropyHistory,
    regimeHistory,
    equityCurve,
    equityTimestamps,
    portfolioValue,
    cash,
    unrealizedPnl,
    realizedPnl,
    totalPnlPct,
    dailyReturnPct,
    exposurePct,
    leverage,
    maxDrawdownPct,
    dailyLossPct,
    maxDrawdown,
    totalTrades,
    winningTrades,
    winRate,
    monteCarlo,
    tokenStates,
    riskRewardRatio,
    // v0.86 — trader notes (Camino B labels for post-close analysis)
    traderNotes,
    // v82j+ — engine version tag for snapshot traceability
    engineVersion,
  } = useTradingStore()

  // Optimistic local state for the auto-mode switch.
  // Syncs from the store on prop change, but updates immediately on
  // user click so the switch doesn't flicker between snapshots.
  const [autoLocal, setAutoLocal] = useState(autoMode)
  useEffect(() => {
    setAutoLocal(autoMode)
  }, [autoMode])

  const handleAutoToggle = (checked: boolean) => {
    setAutoLocal(checked) // immediate visual feedback
    onToggleAuto(checked)
  }

  const modeColor = engineMode === 'live'
    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
    : engineMode === 'paper'
    ? 'bg-blue-500/20 text-blue-400 border-blue-500/30'
    : engineMode === 'demo'
    ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
    : 'bg-red-500/20 text-red-400 border-red-500/30'

  const modeLabel = engineMode === 'live' ? 'LIVE'
    : engineMode === 'paper' ? 'PAPER'
    : engineMode === 'demo' ? 'DEMO'
    : 'OFFLINE'

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

        {/* v82j+: engine version badge — at-a-glance identification of running build */}
        {engineVersion && (
          <Badge
            variant="outline"
            className="text-[9px] font-mono text-gray-500 border-gray-700 px-1.5 py-0 hidden md:inline-flex"
            title={`Engine: ${engineVersion.summary}\nBuilt: ${engineVersion.built_at}\nStack: ${engineVersion.strategy_stack}`}
          >
            {engineVersion.strategy_stack} @ {engineVersion.git_short}
          </Badge>
        )}

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
            checked={autoLocal}
            onCheckedChange={handleAutoToggle}
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

        <Button
          size="sm"
          variant="outline"
          onClick={() => exportDebugSnapshot()}
          className="h-7 text-xs font-mono gap-1 bg-[#1e2a3d] hover:bg-[#2a3a5d] border-[#3a4a6d] text-gray-300"
          title="Copy full engine snapshot to clipboard for AI debugging"
        >
          <ClipboardCopy className="w-3 h-3" />
          EXPORT
        </Button>
      </div>
    </header>
  )

  /**
   * Build a comprehensive engine-state snapshot and copy it to the clipboard
   * as a fenced markdown JSON code block, ready to paste back in chat for
   * AI analysis.
   *
   * Captures: engine health, money, strategies A/B/C/D, positions, trades,
   * patterns/brain, machine learning, signals, risk, tokens, loop health,
   * monte carlo. Everything the AI needs to diagnose if the engine is
   * operating correctly and producing the expected results.
   */
  function exportDebugSnapshot() {
    const now = Date.now()
    const iso = new Date(now).toISOString()
    const ts = new Date(now).toLocaleString('es-ES', { hour12: false })

    // ─── 1. ENGINE HEALTH ─────────────────────────────────
    const secondsSinceLastTick = lastTickAt
      ? Math.round((now - lastTickAt) / 1000)
      : null
    // Ticks per minute: derive from tickCount + first observation time
    // (we approximate using candlesProcessed as proxy if tickCount is 0)
    // FIX v13 BUG O: Old formula used (lastTickAt - 60000) which is just 1 min
    //   before the last tick → denominator ≈ 1 → tickCount/min = tickCount.
    //   Reported 212679 ticks/min (impossible). Use session length instead.
    //   We don't have engine start time here, so derive from candles_processed
    //   (1 candle per 1.5s tick interval).
    const sessionMin = tickCount ? tickCount * 1.5 / 60 : 0
    const tickRatePerMin = (tickCount && sessionMin > 0)
      ? +(tickCount / sessionMin).toFixed(1)
      : 0

    const meta = {
      exported_at: iso,
      exported_at_local: ts,
      engine_mode: engineMode,
      is_running: isRunning,
      auto_mode: autoMode,
      is_connected: isConnected,
      websocket_status: websocketStatus,
      tick_count: tickCount,
      candles_processed: candlesProcessed,
      last_tick_at: lastTickAt ? new Date(lastTickAt).toISOString() : null,
      seconds_since_last_tick: secondsSinceLastTick,
      tick_rate_per_min_approx: tickRatePerMin,
      selected_token: selectedToken,
      active_tokens_count: (activeTokens || []).length,
      time_frame: timeframe,
      exchange,
    }

    // ─── 2. MONEY ──────────────────────────────────────────
    const store = useTradingStore.getState()
    const money = {
      cash_balance: +store.cash?.toFixed(2),
      portfolio_value: +store.portfolioValue?.toFixed(2),
      realized_pnl: +store.realizedPnl?.toFixed(2),
      unrealized_pnl: +store.unrealizedPnl?.toFixed(2),
      total_pnl_pct: +store.totalPnlPct?.toFixed(2),
      daily_return_pct: +store.dailyReturnPct?.toFixed(2),
      exposure_pct: +store.exposurePct?.toFixed(2),
      leverage: store.leverage,
      max_drawdown_pct: +store.maxDrawdownPct?.toFixed(2),
      daily_loss_pct: +store.dailyLossPct?.toFixed(2),
      max_drawdown_seen: +store.maxDrawdown?.toFixed(2),
      kelly_pct: +store.kellyPercent?.toFixed(2),
      suggested_position_size_usdt: +store.suggestedPositionSize?.toFixed(2),
      risk_reward_ratio: +store.riskRewardRatio?.toFixed(2),
      is_trading_allowed: store.isTradingAllowed,
      kill_switch_active: store.killSwitchActive,
    }

    // ─── 3. STRATEGIES A/B/C/D ─────────────────────────────
    // Already in strategies_perf, just normalize numbers
    const strategies = Object.fromEntries(
      Object.entries(strategies_perf || {}).map(([k, v]: [string, any]) => [
        k,
        {
          name: v.name,
          description: v.description,
          cash: +v.cash?.toFixed(2),
          allocated: +v.allocated?.toFixed(2),
          realized_pnl: +v.realized_pnl?.toFixed(2),
          unrealized_pnl: +v.unrealized_pnl?.toFixed(2),
          total_pnl_pct: +v.total_pnl_pct?.toFixed(2),
          total_trades: v.total_trades,
          winning_trades: v.winning_trades,
          win_rate: +v.win_rate?.toFixed(1),
          open_positions: v.open_positions,
          last_signal_age_min: v.last_signal_time
            ? Math.round((now - v.last_signal_time) / 60000)
            : null,
          color: v.color,
        },
      ])
    )

    // ─── 4. POSITIONS (open) ───────────────────────────────
    const openPositions = (positions || [])
      .filter((p: any) => p.status === 'OPEN')
      .map((p: any) => ({
        symbol: p.symbol,
        direction: p.direction,
        strategy: p.strategy || '?',
        entry_price: +p.entry_price?.toFixed(4),
        current_sl: p.current_sl ? +p.current_sl.toFixed(4) : null,
        current_tp: p.current_tp ? +p.current_tp.toFixed(4) : null,
        catastrophic_sl: p.catastrophic_sl ? +p.catastrophic_sl.toFixed(4) : null,
        size_usdt: +p.size_usdt?.toFixed(2),
        pnl_pct: +p.pnl_pct?.toFixed(2),
        pnl_usdt: +p.pnl_usdt?.toFixed(2),
        age_min: p.entry_time
          ? Math.round((now - new Date(p.entry_time).getTime()) / 60000)
          : null,
        // Distance to SL/TP in % (how close is it to being hit)
        sl_distance_pct: (p.current_sl && p.entry_price)
          ? +Math.abs((p.current_sl - p.entry_price) / p.entry_price * 100).toFixed(2)
          : null,
        tp_distance_pct: (p.current_tp && p.entry_price)
          ? +Math.abs((p.current_tp - p.entry_price) / p.entry_price * 100).toFixed(2)
          : null,
        // For LONG: how far is current price from SL/TP
        // (positive = profit moving toward TP, negative = loss toward SL)
        current_price: +store.currentPrice?.toFixed(4) || null,
      }))

    // ─── 5. TRADES (last 50 closed + aggregate stats) ─────
    const recentTrades = (tradeHistory || [])
      .slice(-50)
      .map((t: any) => {
        // v0.86: attach trader note (label + text) if present for this trade.
        // Key matches the chart modal: `${symbol}__${entry_time}`.
        const noteKey = `${t.symbol}__${t.entry_time}`
        const note = traderNotes?.[noteKey]
        return {
          symbol: t.symbol,
          direction: t.direction,
          status: t.status,
          entry_price: +t.entry_price?.toFixed(4),
          close_price: t.close_price ? +t.close_price.toFixed(4) : null,
          close_reason: t.close_reason || null,
          pnl_pct: +t.pnl_pct?.toFixed(2),
          pnl_usdt: +t.pnl_usdt?.toFixed(2),
          size_usdt: +t.size_usdt?.toFixed(2),
          opened_at: t.entry_time,
          closed_at: t.closed_at || null,
          hold_min: (t.entry_time && t.closed_at)
            ? Math.round((new Date(t.closed_at).getTime() - new Date(t.entry_time).getTime()) / 60000)
            : null,
          // v0.86: trader note (null if untagged)
          trader_note: note ? {
            label: note.label,
            text: note.text,
            updated_at: new Date(note.updated_at).toISOString(),
          } : null,
        }
      })

    // Aggregate trade stats
    const allTrades = tradeHistory || []
    const closedTrades = allTrades.filter((t: any) => t.status === 'CLOSED')
    const wins = closedTrades.filter((t: any) => t.pnl_usdt > 0)
    const losses = closedTrades.filter((t: any) => t.pnl_usdt < 0)
    // FIX v10: strip 'CLOSED_BY_' prefix from reasons before classifying
    const normalizeReason = (r: string | undefined): string => {
      if (!r) return ''
      return r.replace(/^CLOSED_BY_/, '')
    }
    const slHits = closedTrades.filter((t: any) => {
      const r = normalizeReason(t.close_reason)
      return r === 'SL' || r === 'STOP_LOSS'
    })
    const tpHits = closedTrades.filter((t: any) => {
      const r = normalizeReason(t.close_reason)
      return r === 'TP' || r === 'TAKE_PROFIT'
    })
    const catSLHits = closedTrades.filter((t: any) => {
      const r = normalizeReason(t.close_reason)
      return r === 'CAT_SL' || r === 'CATASTROPHIC_SL'
    })
    const trailingHits = closedTrades.filter((t: any) => {
      const r = normalizeReason(t.close_reason)
      return r === 'TRAILING' || r === 'TRAILING_STOP'
    })
    const timeStops = closedTrades.filter((t: any) => {
      const r = normalizeReason(t.close_reason)
      return r === 'TIME_STOP'
    })

    // Win/loss streaks (most recent)
    const recentStreaks: any[] = []
    let curStreak = { type: '', count: 0 }
    for (const t of closedTrades.slice(-20)) {
      const type = t.pnl_usdt > 0 ? 'W' : (t.pnl_usdt < 0 ? 'L' : 'BE')
      if (curStreak.type === type) curStreak.count++
      else {
        if (curStreak.count > 0) recentStreaks.push({ ...curStreak })
        curStreak = { type, count: 1 }
      }
    }
    if (curStreak.count > 0) recentStreaks.push({ ...curStreak })

    const tradeStats = {
      total_closed: closedTrades.length,
      total_wins: wins.length,
      total_losses: losses.length,
      win_rate_pct: closedTrades.length ? +((wins.length / closedTrades.length) * 100).toFixed(1) : 0,
      avg_win_usdt: wins.length ? +(wins.reduce((s: number, t: any) => s + t.pnl_usdt, 0) / wins.length).toFixed(2) : 0,
      avg_loss_usdt: losses.length ? +(losses.reduce((s: number, t: any) => s + t.pnl_usdt, 0) / losses.length).toFixed(2) : 0,
      best_trade_usdt: closedTrades.length ? +Math.max(...closedTrades.map((t: any) => t.pnl_usdt)).toFixed(2) : 0,
      worst_trade_usdt: closedTrades.length ? +Math.min(...closedTrades.map((t: any) => t.pnl_usdt)).toFixed(2) : 0,
      profit_factor: losses.length
        ? +(Math.abs(wins.reduce((s: number, t: any) => s + t.pnl_usdt, 0)) / Math.abs(losses.reduce((s: number, t: any) => s + t.pnl_usdt, 0))).toFixed(2)
        : (wins.length ? 999 : 0),
      close_reasons: {
        SL: slHits.length,
        TP: tpHits.length,
        CAT_SL: catSLHits.length,
        TRAILING: trailingHits.length,
        TIME_STOP: timeStops.length,
        MANUAL: closedTrades.filter((t: any) => t.close_reason === 'MANUAL' || t.close_reason === 'CLOSED').length,
        other: closedTrades.filter((t: any) => {
          const r = normalizeReason(t.close_reason)
          return !['SL','STOP_LOSS','TP','TAKE_PROFIT','CAT_SL','CATASTROPHIC_SL','TRAILING','TRAILING_STOP','TIME_STOP','MANUAL','CLOSED'].includes(r)
        }).length,
      },
      recent_streaks: recentStreaks.slice(-5),
      avg_hold_min: closedTrades.length
        ? +Math.round(closedTrades.reduce((s: number, t: any) => {
            if (!t.entry_time || !t.closed_at) return s
            return s + (new Date(t.closed_at).getTime() - new Date(t.entry_time).getTime()) / 60000
          }, 0) / closedTrades.length)
        : 0,
    }

    // ─── 6. PATTERNS / BRAIN ───────────────────────────────
    const patterns = {
      pattern_buffer: patternBuffer || [],
      pattern_buffer_length: (patternBuffer || []).length,
      entropy: +entropy?.toFixed(4),
      regime: regime || null,
      // Living Trie stats
      living_trie: livingTrieStats ? {
        pattern_count: livingTrieStats.pattern_count,
        max_depth: livingTrieStats.max_depth,
        trading_observations: livingTrieStats.trading_observations,
        last_update: livingTrieStats.last_update,
      } : null,
      // Pattern match history (last 20 — trend of pattern detection quality)
      pattern_match_recent: (patternMatchHistory || []).slice(-20).map((p: any) => ({
        time: p.time ? new Date(p.time * 1000).toISOString() : null,
        match_score: +p.matchScore?.toFixed(4),
        path_length: p.pathLength,
      })),
      // Entropy trend (last 20)
      entropy_trend: (entropyHistory || []).slice(-20).map((p: any) => ({
        time: p.time ? new Date(p.time * 1000).toISOString() : null,
        entropy: +p.value?.toFixed(4),
      })),
      // Regime distribution (count of each regime in last 50)
      regime_distribution: (regimeHistory || []).slice(-50).reduce((acc: any, p: any) => {
        acc[p.regime] = (acc[p.regime] || 0) + 1
        return acc
      }, {} as Record<string, number>),
    }

    // ─── 7. MACHINE LEARNING ───────────────────────────────
    const ml = {
      learning_stage: learningStage,
      drift_detected: driftDetected,
      last_retrain_time: lastRetrainTime ? new Date(lastRetrainTime).toISOString() : null,
      last_retrain_age_min: lastRetrainTime
        ? Math.round((now - lastRetrainTime) / 60000)
        : null,
      // Learning stage history (transitions over time)
      learning_stage_history: (learningStageHistory || []).slice(-20).map((p: any) => ({
        time: p.time ? new Date(p.time * 1000).toISOString() : null,
        stage: p.stage,
      })),
      // Confidence trend (recent signal confidence values)
      confidence_trend: (confidenceHistory || []).slice(-20).map((p: any) => ({
        time: p.time ? new Date(p.time * 1000).toISOString() : null,
        confidence: +p.confidence?.toFixed(3),
        ev_score: +p.evScore?.toFixed(3),
        direction: p.direction,
      })),
      // Win rate trend (last 20)
      win_rate_trend: (winRateHistory || []).slice(-20).map((p: any) => ({
        time: p.time ? new Date(p.time * 1000).toISOString() : null,
        win_rate: +p.winRate?.toFixed(1),
        total_trades: p.trades,
        total_wins: p.wins,
      })),
    }

    // ─── 8. SIGNALS (recent) ───────────────────────────────
    const recentSignals = (signalsHistory || []).slice(-20).map((s: any) => ({
      timestamp: s.timestamp,
      symbol: s.symbol,
      direction: s.direction,
      confidence: +s.confidence?.toFixed(3),
      ev_score: +s.ev_score?.toFixed(3),
      pattern_path: s.pattern_path,
      expected_move_pct: +s.expected_move_pct?.toFixed(2),
    }))
    const signals = {
      latest_signal: latestSignal ? {
        timestamp: latestSignal.timestamp,
        symbol: latestSignal.symbol,
        direction: latestSignal.direction,
        confidence: +latestSignal.confidence?.toFixed(3),
        ev_score: +latestSignal.ev_score?.toFixed(3),
        pattern_path: latestSignal.pattern_path,
        expected_move_pct: +latestSignal.expected_move_pct?.toFixed(2),
      } : null,
      total_signals: (signalsHistory || []).length,
      recent: recentSignals,
      signal_rate_per_hour: (signalsHistory && signalsHistory.length > 1)
        ? +((signalsHistory.length) / Math.max(1, (now - new Date(signalsHistory[0].timestamp).getTime()) / 3600000)).toFixed(1)
        : 0,
    }

    // ─── 9. RISK ───────────────────────────────────────────
    const risk = {
      circuit_breakers: circuitBreakers,
      money_manager: moneyManager,
      monte_carlo: monteCarlo ? {
        risk_of_ruin: +monteCarlo.risk_of_ruin?.toFixed(4),
        probability_of_profit: +monteCarlo.probability_of_profit?.toFixed(4),
        p95_dd: +monteCarlo.p95_dd?.toFixed(2),
        verdict: monteCarlo.verdict,
      } : null,
    }

    // ─── 10. TOKENS (active ones with P&L) ─────────────────
    const tokenList = Object.values(tokenStates || {})
      .filter((t: any) => t.isActive || (t.positions && t.positions.length > 0))
      .map((t: any) => ({
        symbol: t.symbol,
        name: t.name,
        price: +t.price?.toFixed(4),
        change_24h_pct: +t.change24h?.toFixed(2),
        volume_24h_usdt: t.volume24h ? +Math.round(t.volume24h).toExponential(2) : null,
        positions_open: (t.positions || []).length,
        unrealized_pnl: +t.unrealizedPnl?.toFixed(2),
        realized_pnl: +t.realizedPnl?.toFixed(2),
        allocation_pct: +t.allocationPct?.toFixed(1),
        win_rate: +t.winRate?.toFixed(1),
        total_trades: t.totalTrades,
        is_trading: t.isTrading,
      }))
      .sort((a: any, b: any) => Math.abs(b.unrealized_pnl) - Math.abs(a.unrealized_pnl))
      .slice(0, 30)  // top 30 by |PnL|

    const tokens = {
      active_count: (activeTokens || []).length,
      list: tokenList,
    }

    // ─── 11. LOOP HEALTH ───────────────────────────────────
    const loopHealth = {
      // Equity curve sample (last 30 points to see recent trajectory)
      equity_curve_recent: (equityCurve || []).slice(-30).map((v: number) => +v.toFixed(2)),
      equity_curve_length: (equityCurve || []).length,
      // Equity delta (last 30 points)
      equity_delta_recent: (() => {
        const recent = (equityCurve || []).slice(-30)
        if (recent.length < 2) return 0
        return +(recent[recent.length - 1] - recent[0]).toFixed(2)
      })(),
      // Loop tick freshness
      is_loop_alive: secondsSinceLastTick !== null && secondsSinceLastTick < 60,
      // First vs last tick (session length)
      session_length_min: (equityTimestamps && equityTimestamps.length > 1)
        ? +((equityTimestamps[equityTimestamps.length - 1] - equityTimestamps[0]) / 60).toFixed(1)
        : 0,
    }

    // ─── 12. AI HINTS (auto-detected anomalies) ────────────
    // Things the AI should look at first
    const hints: string[] = []
    if (secondsSinceLastTick !== null && secondsSinceLastTick > 60) {
      hints.push(`⚠️ LOOP STALLED: ${secondsSinceLastTick}s since last tick — WebSocket may be disconnected`)
    }
    if (websocketStatus !== 'connected' && websocketStatus !== 'open') {
      hints.push(`⚠️ WEBSOCKET NOT CONNECTED: status="${websocketStatus}" — engine cannot receive prices`)
    }
    if (!isRunning) hints.push('⚠️ ENGINE NOT RUNNING — click START to begin')
    if (killSwitchActive) hints.push('⚠️ KILL SWITCH ACTIVE — manual reset required')
    if (circuitBreakers?.max_drawdown) hints.push('⚠️ CIRCUIT BREAKER: max_drawdown — new entries blocked')
    if (circuitBreakers?.daily_loss) hints.push('⚠️ CIRCUIT BREAKER: daily_loss — new entries blocked')
    if (!autoMode) hints.push('ℹ️ AUTO MODE OFF — engine will not place new trades automatically')
    if (openPositions.length === 0 && (autoMode && isRunning)) {
      hints.push('ℹ️ NO OPEN POSITIONS — engine running but no entries (may be in cooldown or no signals)')
    }
    if (tradeStats.profit_factor > 0 && tradeStats.profit_factor < 1) {
      hints.push(`⚠️ PROFIT FACTOR < 1 (${tradeStats.profit_factor}) — strategy is losing money`)
    }
    if (catSLHits.length > 0) {
      hints.push(`⚠️ ${catSLHits.length} CATASTROPHIC SL HITS — positions blew past normal SL`)
    }
    if (driftDetected) hints.push('⚠️ MODEL DRIFT DETECTED — retraining recommended')
    if (learningStage === 'BOOTSTRAP') {
      hints.push('ℹ️ ML STAGE: BOOTSTRAP — engine still collecting observations, signals may be weak')
    }
    if ((strategies_perf || {}).A?.cash === 3000 && (strategies_perf || {}).A?.total_trades === 0) {
      hints.push('ℹ️ STRATEGY A has not traded yet — may need more time or signal thresholds too strict')
    }
    // Check if any strategy is dead (no trades in a long time)
    Object.entries(strategies_perf || {}).forEach(([k, v]: [string, any]) => {
      if (v.last_signal_time) {
        const ageMin = Math.round((now - v.last_signal_time) / 60000)
        if (ageMin > 30) hints.push(`ℹ️ STRATEGY ${k} last signal was ${ageMin} min ago`)
      }
    })

    // ─── Assemble final snapshot ───────────────────────────
    // v0.86: trader_notes section — aggregates all user-tagged trades so
    // the AI can quickly see the distribution of labels (BAD_ENTRY / BAD_SL
    // / BAD_TP / GOOD_TRADE) and correlate them with trade features.
    const traderNotesEntries = Object.entries(traderNotes || {})
    const traderNotesAgg = {
      total_tagged: traderNotesEntries.length,
      by_label: {
        BAD_ENTRY:  traderNotesEntries.filter(([, n]: [string, any]) => n?.label === 'BAD_ENTRY').length,
        BAD_SL:     traderNotesEntries.filter(([, n]: [string, any]) => n?.label === 'BAD_SL').length,
        BAD_TP:     traderNotesEntries.filter(([, n]: [string, any]) => n?.label === 'BAD_TP').length,
        GOOD_TRADE: traderNotesEntries.filter(([, n]: [string, any]) => n?.label === 'GOOD_TRADE').length,
      },
      // Full notes (key + label + text) so the AI can read the free-text
      // comments and correlate them with specific trades.
      all_notes: traderNotesEntries.map(([key, n]: [string, any]) => ({
        trade_key: key,
        label: n?.label ?? null,
        text: n?.text ?? '',
        updated_at: n?.updated_at ? new Date(n.updated_at).toISOString() : null,
      })),
    }

    const snapshot = {
      _version: 'ppmt-export-v9',
      _exported_at: iso,
      _hints: hints,
      // v82j+: engine version tag — identifies which code generated this snapshot.
      // Captured at build time from package.json + strategy stack + git short hash.
      // The AI can read this in one glance to know which fixes are live.
      engine_version: engineVersion ?? {
        strategy_stack: 'unknown',
        pkg_version: 'unknown',
        git_short: 'unknown',
        built_at: 'unknown',
        strategies: {},
        flags: {},
        summary: 'unknown (engine version not reported — likely pre-v82j build)',
      },
      meta,
      money,
      strategies,
      open_positions: openPositions,
      open_positions_count: openPositions.length,
      trades: {
        stats: tradeStats,
        recent: recentTrades,
      },
      // v0.86: trader notes (Camino B) — user tags for post-close analysis
      trader_notes: traderNotesAgg,
      patterns,
      machine_learning: ml,
      signals,
      risk,
      tokens,
      loop_health: loopHealth,
    }

    const json = JSON.stringify(snapshot, null, 2)
    const hintsSection = hints.length
      ? '### Auto-detected issues\n' + hints.map(h => `- ${h}`).join('\n') + '\n\n'
      : ''
    const mdFence = '```'
    // v82j+: include engine version summary in the title for at-a-glance identification
    const versionLine = engineVersion
      ? `\n**Engine:** \`${engineVersion.summary}\`\n`
      : '\n**Engine:** `unknown (pre-v82j build)`\n'
    const markdown = `## PPMT Engine Snapshot v9 — ${ts}\n${versionLine}\n${hintsSection}${mdFence}json\n${json}\n${mdFence}`

    // Copy to clipboard
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(markdown).then(
        () => {
          console.log('%c[PPMT EXPORT v9] Snapshot copied to clipboard', 'color:#4ade80;font-weight:bold;font-size:14px')
          console.log(`%cSize: ${(markdown.length / 1024).toFixed(1)} KB | Hints: ${hints.length}`, 'color:#94a3b8')
          if (hints.length) {
            console.log('%cAuto-detected issues:', 'color:#fbbf24;font-weight:bold')
            hints.forEach(h => console.log('  ' + h))
          }
          console.log('%cFull snapshot:', 'color:#60a5fa')
          console.log(snapshot)
          alert(`✅ Snapshot v9 copiado (${(markdown.length / 1024).toFixed(1)} KB)\n\n${hints.length ? '⚠️ ' + hints.length + ' issues detectados — ver consola para detalle' : '✓ Sin issues detectados'}\n\nPegalo en el chat para análisis.`)
        },
        (err) => {
          console.error('[PPMT EXPORT v9] Clipboard failed:', err)
          const w = window.open('', '_blank')
          if (w) {
            w.document.write(`<pre style="white-space:pre-wrap;font-family:monospace;font-size:11px;padding:16px">${markdown.replace(/</g, '&lt;')}</pre>`)
            w.document.title = 'PPMT Snapshot v9'
          } else {
            alert('No se pudo copiar ni abrir ventana. Abrí la consola (F12) y usá: window.__ppmtSnapshot')
          }
        }
      )
    } else {
      console.log(markdown)
      alert('Clipboard API no disponible. El snapshot se imprimió en la consola (F12).')
    }

    // Also expose globally for manual copy
    ;(window as any).__ppmtSnapshot = markdown
    ;(window as any).__ppmtSnapshotJson = snapshot
  }
}
