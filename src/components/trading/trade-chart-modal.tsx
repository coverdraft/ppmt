/**
 * TradeChartModal — Candlestick chart for an open or closed position.
 *
 * Click any position in PositionPanel or any trade in TradeLog → opens this modal.
 * Shows:
 *   - Candlestick chart of recent price action (Coinbase/Kraken OHLCV via /api/candles)
 *   - Entry price marker (blue solid line + label)
 *   - Take Profit level (green dashed line + label)
 *   - Stop Loss level (red dashed line + label)
 *   - Catastrophic SL (red dotted line, faint)
 *   - Exit price marker (purple dot) — for CLOSED trades only
 *   - Current price marker (yellow dot, pulsing) — for OPEN trades, updates in real-time
 *
 * Real-time updates:
 *   - Candles auto-refresh every 5s from /api/candles (new candles appear live)
 *   - For OPEN positions: the last candle's close updates on every price tick
 *     using tokenStates[symbol].price (NOT the global currentPrice, which is
 *     for the selected symbol only)
 *   - SL/TP lines update live if trailing stop / break-even moves them
 *     (reads from the store's positions array, not the snapshot)
 *
 * Robustness:
 *   - Uses callback ref for the chart container to handle Radix Dialog's
 *     portal mounting timing (container may not be available on first render)
 *   - Uses chartReady state to defer data setting until chart exists
 *   - Incremental updates: setData only when candle count changes (new candle),
 *     series.update() for price-only changes (no chart flicker / zoom reset)
 */
'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type IPriceLine,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useTradingStore, type Position, type TradeRecord, type TraderNote, type TraderNoteLabel } from '@/stores/trading-store'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { ArrowUp, ArrowDown, TrendingUp, TrendingDown, X, Loader2, Radio, StickyNote, Check } from 'lucide-react'

// ─── Helpers ────────────────────────────────────────────────────────────

interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

function fmtPrice(p: number | null | undefined): string {
  if (p === null || p === undefined || isNaN(p)) return '—'
  if (p >= 1000) return p.toFixed(2)
  if (p >= 1) return p.toFixed(4)
  if (p >= 0.01) return p.toFixed(5)
  return p.toFixed(8)
}

function fmtTime(iso: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-ES', {
      day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function timeToSeconds(iso: string | undefined): number | null {
  if (!iso) return null
  try {
    return Math.floor(new Date(iso).getTime() / 1000)
  } catch {
    return null
  }
}

// ─── Component ──────────────────────────────────────────────────────────

export function TradeChartModal() {
  const {
    chartModalTrade, setChartModalTrade,
    currentPrice, symbol: selectedSymbol,
    tokenStates, positions,
    traderNotes, setTraderNote,
  } = useTradingStore()

  // ─── Refs ────────────────────────────────────────────────────────────
  // Use a callback ref + state so the chart-creation effect fires after
  // Radix Dialog mounts the content via portal (containerEl is null on
  // first render, becomes available after portal mount).
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
  const [chartReady, setChartReady] = useState(false)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const entryLineRef = useRef<IPriceLine | null>(null)
  const tpLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)
  const catSlLineRef = useRef<IPriceLine | null>(null)
  const lastCandleTimeRef = useRef<number>(0)
  const lastAppliedCandlesRef = useRef<number>(0)

  // ─── State ───────────────────────────────────────────────────────────
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [interval, setIntervalSel] = useState<'1m' | '5m' | '15m' | '1h'>('5m')
  const [candles, setCandles] = useState<Candle[]>([])

  const isOpen = chartModalTrade !== null
  const trade = chartModalTrade as (Position | TradeRecord) | null

  // ─── Detect open vs closed ───────────────────────────────────────────
  // TradeRecord always has close_price (it's in the interface).
  // Position never has close_price.
  // This is the ONLY reliable way to tell them apart — status can be
  // 'OPEN', 'BREAK_EVEN_SECURED', 'TRAILING', etc. for open positions.
  const isClosedTrade = !!trade && (trade as TradeRecord).close_price !== undefined && (trade as TradeRecord).close_price !== null
  const isOpenPosition = !!trade && !isClosedTrade

  // For open positions, read live SL/TP from the store's positions array
  // (they may have moved due to trailing stop / break-even adjustments)
  const livePosition: Position | null = isOpenPosition && trade
    ? positions.find(p =>
        p.symbol === (trade as Position).symbol &&
        p.direction === (trade as Position).direction &&
        p.entry_time === (trade as Position).entry_time) || null
    : null

  // ─── Live price for the trade's symbol ───────────────────────────────
  // CRITICAL: the store's `currentPrice` is for the SELECTED symbol (e.g., BTC),
  // NOT for the trade's symbol. We must use tokenStates[symbol].price to get
  // the correct live price for the trade's token.
  const tradeSymbol = trade?.symbol || ''
  const tokenPrice = tradeSymbol ? tokenStates[tradeSymbol]?.price || 0 : 0
  const livePrice = tokenPrice > 0
    ? tokenPrice
    : (tradeSymbol === selectedSymbol ? currentPrice : 0)

  // ─── Fetch candles ───────────────────────────────────────────────────
  const fetchCandles = useCallback(() => {
    if (!trade) return
    const url = `/api/candles?symbol=${encodeURIComponent(trade.symbol)}&interval=${interval}&limit=300`
    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (data.error && (!data.candles || data.candles.length === 0)) {
          setError(data.error)
          setLoading(false)
          return
        }
        setError(null)
        const cs: Candle[] = (data.candles || []).map((c: any) => ({
          time: c.time as number,
          open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
        }))
        setCandles(cs)
        setLoading(false)
      })
      .catch(e => {
        setError('Fetch failed: ' + (e?.message || 'unknown'))
        setLoading(false)
      })
  }, [trade, interval])

  // Initial fetch + poll every 5s for fresh candles while modal is open
  useEffect(() => {
    if (!isOpen || !trade) return
    setLoading(true)
    setCandles([])
    setError(null)
    lastCandleTimeRef.current = 0
    lastAppliedCandlesRef.current = 0
    fetchCandles()
    const poll = setInterval(fetchCandles, 5000)
    return () => clearInterval(poll)
  }, [isOpen, trade, interval, fetchCandles])

  // ─── Create chart when container is ready ────────────────────────────
  useEffect(() => {
    if (!isOpen || !containerEl) return

    const chart = createChart(containerEl, {
      layout: {
        background: { type: ColorType.Solid, color: '#0d1117' },
        textColor: '#9ca3af',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 42, 61, 0.5)' },
        horzLines: { color: 'rgba(30, 42, 61, 0.5)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#6b7280', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#1e2a3d' },
        horzLine: { color: '#6b7280', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#1e2a3d' },
      },
      rightPriceScale: {
        borderColor: '#1e2a3d',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#1e2a3d',
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerEl.clientWidth,
      height: 420,
    })
    chartRef.current = chart

    const series = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })
    seriesRef.current = series
    setChartReady(true)

    // ─── Resize observer ──────────────────────────────────────────────
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const w = entry.contentRect.width
        if (chartRef.current && w > 0) {
          chartRef.current.applyOptions({ width: w })
        }
      }
    })
    resizeObserver.observe(containerEl)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      entryLineRef.current = null
      tpLineRef.current = null
      slLineRef.current = null
      catSlLineRef.current = null
      lastCandleTimeRef.current = 0
      lastAppliedCandlesRef.current = 0
      setChartReady(false)
    }
  }, [isOpen, containerEl])

  // ─── Apply candle data (incremental: setData on new candle, update on price change) ─
  useEffect(() => {
    if (!chartReady || !seriesRef.current || candles.length === 0) return

    const lastCandle = candles[candles.length - 1]
    const lastTime = lastCandle.time

    // Full setData when:
    //   - first load (lastAppliedCandlesRef === 0)
    //   - new candle arrived (lastTime changed)
    //   - candle count changed significantly (e.g., interval switch)
    if (lastTime !== lastCandleTimeRef.current || lastAppliedCandlesRef.current === 0) {
      const data = candles.map(c => ({
        time: c.time as UTCTimestamp,
        open: c.open, high: c.high, low: c.low, close: c.close,
      }))
      seriesRef.current.setData(data)
      lastCandleTimeRef.current = lastTime
      lastAppliedCandlesRef.current = candles.length

      // Fit content on first load only (don't reset zoom on every new candle)
      if (chartRef.current && lastAppliedCandlesRef.current === candles.length) {
        // Only fitContent if this is the initial load (chart was empty before)
        // We detect this by checking if we had 0 candles before
      }

      // Re-fit on first load
      if (chartRef.current && candles.length > 0 && lastAppliedCandlesRef.current === candles.length) {
        // Only fitContent once per chart session
        // (subsequent new candles just extend the view)
      }

      // Add exit marker for closed trades (only on full setData)
      if (isClosedTrade && trade) {
        const exitTime = timeToSeconds((trade as TradeRecord).closed_at)
        const exitPrice = (trade as TradeRecord).close_price
        if (exitTime && exitPrice) {
          // Check if exit time is within the candle range
          const firstTime = candles[0].time
          const lastCandleTime = candles[candles.length - 1].time
          if (exitTime >= firstTime && exitTime <= lastCandleTime + (lastTime - candles[candles.length - 2]?.time || 60)) {
            try {
              seriesRef.current.setMarkers([{
                time: exitTime as UTCTimestamp,
                position: 'aboveBar',
                color: '#a855f7',
                shape: 'circle',
                text: `EXIT ${fmtPrice(exitPrice)}`,
              }])
            } catch (e) {
              // Marker time must exist in the data series
            }
          }
        }
      }
    } else {
      // Same candle, just updated close — use update() (no zoom reset)
      try {
        seriesRef.current.update({
          time: lastCandle.time as UTCTimestamp,
          open: lastCandle.open, high: lastCandle.high, low: lastCandle.low, close: lastCandle.close,
        })
      } catch (e) {
        // Ignore
      }
    }

    // Fit content on first load only
    if (chartRef.current && lastAppliedCandlesRef.current === candles.length && candles.length > 0) {
      // Check if we should fitContent (only when chart was just created)
      // We use a ref to track if we've already fit
      if (!(chartRef.current as any).__ppmtFitted) {
        chartRef.current.timeScale().fitContent()
        ;(chartRef.current as any).__ppmtFitted = true
      }
    }
  }, [chartReady, candles, trade, isClosedTrade])

  // ─── Update price lines (entry / TP / SL / cat SL) ───────────────────
  // Reads from `livePosition` if open, otherwise from `trade` snapshot.
  useEffect(() => {
    const series = seriesRef.current
    if (!series || !trade || !chartReady) return

    // Clear old lines
    if (entryLineRef.current) { series.removePriceLine(entryLineRef.current); entryLineRef.current = null }
    if (tpLineRef.current)    { series.removePriceLine(tpLineRef.current);    tpLineRef.current = null }
    if (slLineRef.current)    { series.removePriceLine(slLineRef.current);    slLineRef.current = null }
    if (catSlLineRef.current) { series.removePriceLine(catSlLineRef.current); catSlLineRef.current = null }

    const entryPrice = (trade as any).entry_price
    if (entryPrice !== undefined && entryPrice !== null) {
      entryLineRef.current = series.createPriceLine({
        price: entryPrice,
        color: '#3b82f6',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: 'ENTRY',
      })
    }

    // For open positions, use live SL/TP (may have moved); for closed, use snapshot
    const tp = isOpenPosition ? (livePosition?.current_tp ?? (trade as any).current_tp) : (trade as any).current_tp
    const sl = isOpenPosition ? (livePosition?.current_sl ?? (trade as any).current_sl) : (trade as any).current_sl
    const catSl = isOpenPosition ? (livePosition?.catastrophic_sl ?? (trade as any).catastrophic_sl) : (trade as any).catastrophic_sl

    if (tp !== null && tp !== undefined) {
      tpLineRef.current = series.createPriceLine({
        price: tp,
        color: '#10b981',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'TP',
      })
    }
    if (sl !== null && sl !== undefined) {
      slLineRef.current = series.createPriceLine({
        price: sl,
        color: '#ef4444',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'SL',
      })
    }
    if (catSl !== null && catSl !== undefined) {
      catSlLineRef.current = series.createPriceLine({
        price: catSl,
        color: '#7f1d1d',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: 'CAT SL',
      })
    }
  }, [trade, livePosition, isOpenPosition, chartReady, candles])

  // ─── Real-time price marker for open positions ───────────────────────
  // Updates the last candle's close to the live price on each tick.
  // Uses tokenStates[symbol].price (NOT the global currentPrice).
  useEffect(() => {
    if (!isOpenPosition || !trade || !seriesRef.current || !chartReady || candles.length === 0) return
    if (livePrice <= 0) return

    const lastCandle = candles[candles.length - 1]
    try {
      seriesRef.current.update({
        time: lastCandle.time as UTCTimestamp,
        open: lastCandle.open,
        high: Math.max(lastCandle.high, livePrice),
        low: Math.min(lastCandle.low, livePrice),
        close: livePrice,
      })
    } catch (e) {
      // Ignore — typically "time already passed" errors when crossing candle boundaries
    }
  }, [livePrice, isOpenPosition, trade, chartReady, candles])

  if (!trade) return null

  // ─── Derived display values ──────────────────────────────────────────
  const isLong = (trade as any).direction === 'LONG'
  const entryPrice = (trade as any).entry_price
  const entryTime = (trade as any).entry_time
  const sizeUsdt = (trade as any).size_usdt
  // For open positions, use LIVE P&L from the store's current positions array
  // (not the stale snapshot from click time). For closed trades, use the
  // historical P&L from the trade record (it never changes after close).
  const pnlPct = isOpenPosition
    ? (livePosition?.pnl_pct ?? (trade as any).pnl_pct ?? 0)
    : ((trade as any).pnl_pct ?? 0)
  const pnlUsdt = isOpenPosition
    ? (livePosition?.pnl_usdt ?? (trade as any).pnl_usdt ?? 0)
    : ((trade as any).pnl_usdt ?? 0)
  const isWin = pnlPct >= 0

  const tp = isOpenPosition ? (livePosition?.current_tp ?? (trade as any).current_tp) : (trade as any).current_tp
  const sl = isOpenPosition ? (livePosition?.current_sl ?? (trade as any).current_sl) : (trade as any).current_sl
  const catSl = isOpenPosition ? (livePosition?.catastrophic_sl ?? (trade as any).catastrophic_sl) : (trade as any).catastrophic_sl
  const exitPrice = isClosedTrade ? (trade as TradeRecord).close_price : null
  const exitTime = isClosedTrade ? (trade as TradeRecord).closed_at : null
  const closeReason = isClosedTrade ? (trade as TradeRecord).close_reason : null

  const strategyLabel = (trade as any).strategy || '—'
  const statusLabel = isOpenPosition
    ? ((trade as Position).status || 'OPEN')
    : 'CLOSED'

  // Distance calculations using the CORRECT per-symbol live price
  const distToTP = tp !== null && tp !== undefined && entryPrice > 0
    ? (isLong ? ((tp - entryPrice) / entryPrice) * 100 : ((entryPrice - tp) / entryPrice) * 100)
    : null
  const distToSL = sl !== null && sl !== undefined && entryPrice > 0
    ? (isLong ? ((sl - entryPrice) / entryPrice) * 100 : ((entryPrice - sl) / entryPrice) * 100)
    : null
  const distToLive = livePrice > 0 && entryPrice > 0
    ? (isLong ? ((livePrice - entryPrice) / entryPrice) * 100 : ((entryPrice - livePrice) / entryPrice) * 100)
    : null

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) setChartModalTrade(null) }}>
      <DialogContent className="bg-[#0d1117] border-[#1e2a3d] text-gray-200 max-w-5xl w-[95vw] p-0 overflow-hidden">
        {/* Header */}
        <DialogHeader className="px-4 py-3 border-b border-[#1e2a3d]">
          <div className="flex items-center gap-3 flex-wrap">
            {isLong ? (
              <ArrowUp className="w-5 h-5 text-emerald-400" />
            ) : (
              <ArrowDown className="w-5 h-5 text-red-400" />
            )}
            <DialogTitle className="font-mono text-base text-gray-100">
              {trade.symbol}
            </DialogTitle>
            <Badge
              variant="outline"
              className={`text-[10px] font-mono ${
                isLong
                  ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                  : 'bg-red-500/20 text-red-400 border-red-500/30'
              }`}
            >
              {isLong ? 'LONG' : 'SHORT'}
            </Badge>
            <Badge
              variant="outline"
              className={`text-[10px] font-mono ${
                isOpenPosition
                  ? 'bg-blue-500/20 text-blue-400 border-blue-500/30'
                  : 'bg-gray-700/40 text-gray-300 border-gray-600'
              }`}
            >
              {statusLabel}
            </Badge>
            {isOpenPosition && (
              <Badge variant="outline" className="text-[9px] font-mono bg-yellow-500/10 text-yellow-400 border-yellow-500/30 flex items-center gap-1">
                <Radio className="w-2.5 h-2.5 animate-pulse" />
                LIVE
              </Badge>
            )}
            {strategyLabel !== '—' && (
              <Badge variant="outline" className="text-[10px] font-mono bg-purple-500/20 text-purple-400 border-purple-500/30">
                STRAT {strategyLabel}
              </Badge>
            )}
            <div className="ml-auto flex items-center gap-1">
              {(['1m', '5m', '15m', '1h'] as const).map(tf => (
                <button
                  key={tf}
                  onClick={() => setIntervalSel(tf)}
                  className={`px-2 py-0.5 text-[10px] font-mono rounded transition-colors ${
                    interval === tf
                      ? 'bg-blue-500/30 text-blue-300 border border-blue-500/50'
                      : 'bg-[#121a26] text-gray-400 border border-[#1e2a3d] hover:bg-[#1a2334]'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>
          <DialogDescription className="text-[10px] text-gray-500 font-mono mt-1">
            Entry: {fmtTime(entryTime)} · Size: ${sizeUsdt?.toFixed(2) || '—'} USDT
            {isClosedTrade && exitTime && ` · Exit: ${fmtTime(exitTime)}`}
            {isClosedTrade && closeReason && ` · ${closeReason}`}
            {isOpenPosition && livePrice > 0 && distToLive !== null && (
              <span className={distToLive >= 0 ? 'text-emerald-400 ml-2' : 'text-red-400 ml-2'}>
                · Live: {fmtPrice(livePrice)} ({distToLive >= 0 ? '+' : ''}{distToLive.toFixed(3)}%)
              </span>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* Chart area */}
        <div className="px-4 py-3">
          {loading && candles.length === 0 && (
            <div className="flex items-center justify-center h-[420px] text-gray-400">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              <span className="font-mono text-xs">Loading candles from {trade.symbol}...</span>
            </div>
          )}

          {error && candles.length === 0 && (
            <div className="flex flex-col items-center justify-center h-[420px] text-gray-400">
              <X className="w-8 h-8 text-red-500 mb-2" />
              <div className="font-mono text-xs text-red-400 mb-1">No candle data available</div>
              <div className="font-mono text-[10px] text-gray-500 max-w-md text-center">{error}</div>
              <div className="font-mono text-[10px] text-gray-600 mt-3 max-w-md text-center">
                This token may not have a Coinbase or Kraken trading pair.
                Try a different timeframe, or pick a token from the top-20 list.
              </div>
            </div>
          )}

          {/* Container is only rendered when we have candles — ensures ref is set after data */}
          {candles.length > 0 && (
            <div ref={setContainerEl} className="w-full h-[420px]" />
          )}
        </div>

        {/* Trade stats panel */}
        <div className="px-4 py-3 border-t border-[#1e2a3d] bg-[#0a0e16]">
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-[10px] font-mono">
            {/* Entry */}
            <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
              <div className="text-gray-500 mb-0.5">ENTRY</div>
              <div className="text-blue-400 text-sm">{fmtPrice(entryPrice)}</div>
              <div className="text-gray-600 text-[9px] mt-0.5">{fmtTime(entryTime)}</div>
            </div>

            {/* Take Profit */}
            <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
              <div className="text-gray-500 mb-0.5">TAKE PROFIT</div>
              <div className="text-emerald-400 text-sm">{fmtPrice(tp)}</div>
              {distToTP !== null && (
                <div className="text-gray-600 text-[9px] mt-0.5">
                  {isLong ? '+' : ''}{distToTP.toFixed(2)}% from entry
                </div>
              )}
            </div>

            {/* Stop Loss */}
            <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
              <div className="text-gray-500 mb-0.5">STOP LOSS</div>
              <div className="text-red-400 text-sm">{fmtPrice(sl)}</div>
              {distToSL !== null && (
                <div className="text-gray-600 text-[9px] mt-0.5">
                  {distToSL >= 0 ? '+' : ''}{distToSL.toFixed(2)}% from entry
                </div>
              )}
            </div>

            {/* Exit (closed) or Current (open) */}
            {isClosedTrade ? (
              <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
                <div className="text-gray-500 mb-0.5">EXIT</div>
                <div className="text-purple-400 text-sm">{fmtPrice(exitPrice)}</div>
                <div className="text-gray-600 text-[9px] mt-0.5">{fmtTime(exitTime || '')}</div>
              </div>
            ) : (
              <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
                <div className="text-gray-500 mb-0.5 flex items-center gap-1">
                  CURRENT
                  {livePrice > 0 && <span className="w-1.5 h-1.5 bg-yellow-400 rounded-full animate-pulse" />}
                </div>
                <div className="text-yellow-400 text-sm">{fmtPrice(livePrice)}</div>
                {distToLive !== null && (
                  <div className={`text-[9px] mt-0.5 ${distToLive >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
                    {distToLive >= 0 ? '+' : ''}{distToLive.toFixed(3)}% from entry
                  </div>
                )}
              </div>
            )}

            {/* Cat SL */}
            <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
              <div className="text-gray-500 mb-0.5">CAT SL</div>
              <div className="text-red-700 text-sm">{fmtPrice(catSl)}</div>
              <div className="text-gray-600 text-[9px] mt-0.5">catastrophic</div>
            </div>

            {/* P&L */}
            <div className={`rounded p-2 border ${isWin ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
              <div className="text-gray-500 mb-0.5">P&amp;L</div>
              <div className={`text-sm flex items-center gap-1 ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                {isWin ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                {isWin ? '+' : ''}{pnlPct.toFixed(3)}%
              </div>
              <div className={`text-[9px] mt-0.5 ${isWin ? 'text-emerald-500' : 'text-red-500'}`}>
                {isWin ? '+' : ''}{pnlUsdt.toFixed(4)} USDT
              </div>
            </div>
          </div>

          {/* Legend */}
          <div className="flex items-center gap-4 mt-3 text-[9px] font-mono text-gray-500 flex-wrap">
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 bg-blue-500 inline-block"></span> Entry
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 border-t border-dashed border-emerald-500 inline-block"></span> Take Profit
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 border-t border-dashed border-red-500 inline-block"></span> Stop Loss
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-0.5 border-t border-dotted border-red-800 inline-block"></span> Cat. SL
            </span>
            {isClosedTrade && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-purple-500 rounded-full inline-block"></span> Exit point
              </span>
            )}
            {isOpenPosition && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-yellow-400 rounded-full inline-block animate-pulse"></span> Live price (updates every tick)
              </span>
            )}
            <span className="ml-auto text-gray-700">
              Source: Coinbase / Kraken · {candles.length} candles · {interval} · auto-refresh 5s
            </span>
          </div>

          {/* ─── Trader Notes (Camino B) ────────────────────────────────────
              Post-close labels the user can attach to a CLOSED trade.
              This does NOT alter the trade — it only annotates the outcome
              so the AI can find systematic patterns in the next EXPORT.
              For OPEN positions, show a disabled hint instead. */}
          {trade && (() => {
            const tradeKey = `${trade.symbol}__${trade.entry_time}`
            return (
              <TraderNotePanel
                tradeKey={tradeKey}
                symbol={trade.symbol}
                entryTime={trade.entry_time}
                isClosed={isClosedTrade}
                note={traderNotes[tradeKey]}
                setTraderNote={setTraderNote}
              />
            )
          })()}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ─── TraderNotePanel ────────────────────────────────────────────────────
// Sub-component for the post-close trader notes UI.
// Extracted so the main modal render stays readable.
//
// Behavior:
//   - 4 quick-label buttons (BAD_ENTRY / BAD_SL / BAD_TP / GOOD_TRADE)
//     Clicking a selected label again deselects it (toggle).
//   - Free-text textarea (optional, max 280 chars).
//   - "Saved" indicator appears briefly after changes (auto-persists on
//     every keystroke via setTraderNote — no explicit Save button needed).
//   - For OPEN positions, the whole panel is collapsed to a hint that
//     says "you can tag this trade after it closes" — we explicitly do
//     NOT allow notes on open positions to avoid contaminating live
//     decisions with pre-judgement.
//
// All state lives in the Zustand store (traderNotes) so it survives
// modal close/reopen and is included in the next EXPORT.

interface TraderNotePanelProps {
  tradeKey: string
  symbol: string
  entryTime: string
  isClosed: boolean
  note: TraderNote | undefined
  setTraderNote: (key: string, note: { label?: TraderNoteLabel | null; text?: string }) => void
}

const LABEL_OPTIONS: { value: TraderNoteLabel; label: string; color: string; hint: string }[] = [
  { value: 'BAD_ENTRY', label: 'Mala entrada', color: 'bg-rose-600/20 text-rose-300 border-rose-600/50', hint: 'Entry timing/zone was wrong' },
  { value: 'BAD_SL',    label: 'SL mal puesto', color: 'bg-amber-600/20 text-amber-300 border-amber-600/50', hint: 'SL too tight / wide / wrong place' },
  { value: 'BAD_TP',    label: 'TP mal puesto', color: 'bg-orange-600/20 text-orange-300 border-orange-600/50', hint: 'TP too tight / wide / wrong place' },
  { value: 'GOOD_TRADE',label: 'Buena gestión', color: 'bg-emerald-600/20 text-emerald-300 border-emerald-600/50', hint: 'Keep doing this (positive label)' },
]

function TraderNotePanel({ tradeKey, symbol, entryTime, isClosed, note, setTraderNote }: TraderNotePanelProps) {
  const [justSaved, setJustSaved] = useState(false)
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Show "Saved ✓" indicator for 1.2s after any change
  const flashSaved = useCallback(() => {
    if (savedTimer.current) clearTimeout(savedTimer.current)
    setJustSaved(true)
    savedTimer.current = setTimeout(() => setJustSaved(false), 1200)
  }, [])

  const onLabelClick = (label: TraderNoteLabel) => {
    // Toggle: if same label clicked again, deselect (set to null)
    const newLabel = note?.label === label ? null : label
    setTraderNote(tradeKey, { label: newLabel })
    flashSaved()
  }

  const onTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const text = e.target.value.slice(0, 280) // hard cap
    setTraderNote(tradeKey, { text })
    flashSaved()
  }

  // ─── Open position: just a hint, no editable UI ─────────────────────
  if (!isClosed) {
    return (
      <div className="mt-3 p-2.5 border border-gray-800 rounded-md bg-gray-900/40">
        <div className="flex items-center gap-2 text-[10px] text-gray-500 font-mono">
          <StickyNote className="w-3 h-3" />
          <span>Trader Note disponible cuando la posición cierre — para etiquetar la operación sin alterarla.</span>
        </div>
      </div>
    )
  }

  // ─── Closed trade: full label UI ────────────────────────────────────
  return (
    <div className="mt-3 p-3 border border-gray-800 rounded-md bg-gray-900/40">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-[11px] text-gray-400 font-mono">
          <StickyNote className="w-3.5 h-3.5 text-amber-400" />
          <span>Trader Note</span>
          <span className="text-gray-700">·</span>
          <span className="text-gray-600">no altera la operación — solo la etiqueta para análisis</span>
        </div>
        {justSaved && (
          <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-mono">
            <Check className="w-3 h-3" /> Saved
          </span>
        )}
      </div>

      {/* Quick-label buttons */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5 mb-2">
        {LABEL_OPTIONS.map(opt => {
          const active = note?.label === opt.value
          return (
            <button
              key={opt.value}
              onClick={() => onLabelClick(opt.value)}
              title={opt.hint}
              className={`px-2 py-1.5 text-[10px] font-mono rounded border transition-colors ${
                active
                  ? opt.color + ' ring-1 ring-offset-0'
                  : 'bg-gray-900/60 text-gray-500 border-gray-800 hover:border-gray-700 hover:text-gray-400'
              }`}
            >
              {opt.label}
            </button>
          )
        })}
      </div>

      {/* Free-text note */}
      <textarea
        value={note?.text ?? ''}
        onChange={onTextChange}
        placeholder="Nota opcional (ej: 'entró justo antes de noticia', 'SL muy tight para ATR=0.8%')..."
        rows={2}
        maxLength={280}
        className="w-full px-2 py-1.5 text-[11px] font-mono bg-gray-950/60 border border-gray-800 rounded text-gray-300 placeholder-gray-700 focus:outline-none focus:border-gray-600 resize-none"
      />
      <div className="flex justify-between items-center mt-1">
        <span className="text-[9px] text-gray-700 font-mono">
          {symbol} · entry {entryTime ? new Date(entryTime).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
        </span>
        <span className="text-[9px] text-gray-700 font-mono">
          {(note?.text ?? '').length}/280
        </span>
      </div>
    </div>
  )
}
