#!/usr/bin/env python3
"""
PPMT Patch v9 — Comprehensive diagnostic EXPORT.

Replaces the basic exportDebugSnapshot with a far richer one that captures
every dimension the AI needs to diagnose engine health:

  1. ENGINE HEALTH      — running, auto, connected, ws status, tick rate
  2. MONEY              — balance, equity, realized PnL, unrealized PnL,
                          daily return, exposure, total return %
  3. STRATEGIES         — A/B/C/D per-strategy cash, P&L, win rate,
                          open count, last signal age
  4. POSITIONS          — all open positions with SL/TP/CatSL, age, strategy
  5. TRADES             — last 50 closed trades with close_reason
                          + aggregated stats (win/loss streaks, avg PnL)
  6. PATTERNS / BRAIN   — patternBuffer, entropy, regime, pattern matches,
                          pattern_match_history trends
  7. MACHINE LEARNING   — learningStage, driftDetected, lastRetrainTime,
                          observations count, learning stage history
  8. SIGNALS            — last 20 generated signals with confidence + EV
  9. RISK               — circuit breakers state, money manager settings,
                          Kelly %, suggested position size, R:R
 10. TOKENS             — active tokens, per-token state (price, PnL,
                          positions, win rate, volume)
 11. LOOP HEALTH        — ticks per minute, seconds since last tick,
                          candles processed, equity curve sample (last 30)
 12. MONTE CARLO        — risk of ruin, prob of profit, p95 drawdown

The snapshot is copied to clipboard as markdown JSON code block, ready
to paste back in chat. Also exposes window.__ppmtSnapshot for fallback.

Files modified:
  1. src/components/trading/header.tsx — replace exportDebugSnapshot fn

Run: python3 /home/z/my-project/scripts/v9_comprehensive_export.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
HEADER = ROOT / "src/components/trading/header.tsx"

if not HEADER.exists():
    print(f"ERROR: header not found at {HEADER}")
    sys.exit(1)

src = HEADER.read_text()

# Find and replace the existing exportDebugSnapshot function block.
# The function starts at "/**\n   * Build a full engine-state snapshot" and
# ends at the final "}\n}" before EOF.

START_MARKER = "  /**\n   * Build a full engine-state snapshot"
END_MARKER = "  function exportDebugSnapshot() {"

# We'll find the function start, then find its closing brace.
start_idx = src.find(START_MARKER)
if start_idx == -1:
    print("ERROR: could not find start of exportDebugSnapshot")
    sys.exit(1)

# Find the "function exportDebugSnapshot() {" line — start of function body
func_body_idx = src.find(END_MARKER, start_idx)
if func_body_idx == -1:
    print("ERROR: could not find function body")
    sys.exit(1)

# Find the closing "}" of the function. The function ends with "  }\n}".
# We need to find the matching close. Simplest: find the LAST "  }\n}" after
# the function body start — the function is the last thing in the file.
end_search_from = func_body_idx
last_close = src.rfind("  }\n}", end_search_from)
if last_close == -1:
    print("ERROR: could not find function close brace")
    sys.exit(1)
# Include the closing "}" of the component function
end_idx = last_close + len("  }\n}")

old_block = src[start_idx:end_idx]
print(f"Found existing exportDebugSnapshot: {len(old_block)} chars, replacing...")

NEW_BLOCK = '''  /**
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
    const tickRatePerMin = (tickCount && lastTickAt)
      ? +(tickCount / Math.max(1, (now - (lastTickAt - 60000)) / 60000)).toFixed(1)
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
      .map((t: any) => ({
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
      }))

    // Aggregate trade stats
    const allTrades = tradeHistory || []
    const closedTrades = allTrades.filter((t: any) => t.status === 'CLOSED')
    const wins = closedTrades.filter((t: any) => t.pnl_usdt > 0)
    const losses = closedTrades.filter((t: any) => t.pnl_usdt < 0)
    const slHits = closedTrades.filter((t: any) => t.close_reason === 'SL' || t.close_reason === 'STOP_LOSS')
    const tpHits = closedTrades.filter((t: any) => t.close_reason === 'TP' || t.close_reason === 'TAKE_PROFIT')
    const catSLHits = closedTrades.filter((t: any) => t.close_reason === 'CAT_SL' || t.close_reason === 'CATASTROPHIC_SL')
    const trailingHits = closedTrades.filter((t: any) => t.close_reason === 'TRAILING' || t.close_reason === 'TRAILING_STOP')
    const timeStops = closedTrades.filter((t: any) => t.close_reason === 'TIME_STOP')

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
        other: closedTrades.filter((t: any) =>
          !['SL','STOP_LOSS','TP','TAKE_PROFIT','CAT_SL','CATASTROPHIC_SL','TRAILING','TRAILING_STOP','TIME_STOP','MANUAL','CLOSED'].includes(t.close_reason)
        ).length,
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
    const snapshot = {
      _version: 'ppmt-export-v9',
      _exported_at: iso,
      _hints: hints,
      meta,
      money,
      strategies,
      open_positions: openPositions,
      open_positions_count: openPositions.length,
      trades: {
        stats: tradeStats,
        recent: recentTrades,
      },
      patterns,
      machine_learning: ml,
      signals,
      risk,
      tokens,
      loop_health: loopHealth,
    }

    const json = JSON.stringify(snapshot, null, 2)
    const markdown = `## PPMT Engine Snapshot v9 — ${ts}\\n\\n${hints.length ? '### Auto-detected issues\\n' + hints.map(h => `- ${h}`).join('\\n') + '\\n\\n' : ''}\`\`\`json\\n${json}\\n\`\`\``

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
          alert(`✅ Snapshot v9 copiado (${(markdown.length / 1024).toFixed(1)} KB)\\n\\n${hints.length ? '⚠️ ' + hints.length + ' issues detectados — ver consola para detalle' : '✓ Sin issues detectados'}\\n\\nPegalo en el chat para análisis.`)
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
}'''

src = src[:start_idx] + NEW_BLOCK + src[end_idx:]

HEADER.write_text(src)
print(f"OK — header.tsx updated ({len(src)} bytes)")
print()
print("=" * 70)
print("  v9 COMPREHENSIVE EXPORT — Applied")
print("=" * 70)
print()
print("Snapshot now captures 12 dimensions:")
print("  1.  meta          — engine health, ws status, tick rate, session info")
print("  2.  money         — balance, equity, PnL (realized+unrealized), exposure")
print("  3.  strategies    — A/B/C/D cash, P&L, win rate, last signal age")
print("  4.  open_positions — SL/TP/CatSL, age, distance to SL/TP in %")
print("  5.  trades        — last 50 closed + aggregate stats (PF, avg win/loss,")
print("                       close_reasons breakdown, win/loss streaks, hold time)")
print("  6.  patterns      — buffer, entropy, regime, Living Trie, trend history")
print("  7.  machine_learning — stage, drift, retrain age, confidence trend,")
print("                          win rate trend, learning stage transitions")
print("  8.  signals       — last 20 generated signals + signal rate per hour")
print("  9.  risk          — circuit breakers, money manager, Monte Carlo")
print(" 10.  tokens        — top 30 tokens by |PnL| with price/volume/win rate")
print(" 11.  loop_health   — equity curve sample, equity delta, session length")
print(" 12.  _hints        — auto-detected anomalies (stalled loop, breakers, etc)")
print()
print("AI auto-hints flag things like:")
print("  - LOOP STALLED (no ticks > 60s)")
print("  - WEBSOCKET DISCONNECTED")
print("  - CIRCUIT BREAKERS active")
print("  - PROFIT FACTOR < 1 (losing money)")
print("  - CATASTROPHIC SL hits (SL not respected)")
print("  - MODEL DRIFT detected")
print("  - ML still in BOOTSTRAP (weak signals)")
print("  - Strategies that haven't traded recently")
print()
print("On your Mac:")
print("  1. git pull origin terminal-web")
print("  2. kill -9 $(lsof -ti :3000) 2>/dev/null; sleep 1; npm run dev")
print("  3. Click EXPORT button in header → snapshot v9 copied to clipboard")
print("  4. Paste in chat — AI gets full 12-dimension view for analysis")
