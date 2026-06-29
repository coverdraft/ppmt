/**
 * TradeChartModal — Candlestick chart for an open or closed position.
 *
 * Click any position in PositionPanel or any trade in TradeLog → opens this modal.
 * Shows:
 *   - Candlestick chart of recent price action (Coinbase/Kraken OHLCV via /api/candles)
 *   - Entry price marker (blue line + label)
 *   - Take Profit level (green dashed line + label)
 *   - Stop Loss level (red dashed line + label)
 *   - Catastrophic SL (red dotted line, faint)
 *   - Exit price marker (purple dot) — for CLOSED trades only
 *   - Current price marker (yellow dot) — for OPEN trades, updates in real-time
 *
 * Real-time updates:
 *   - For OPEN positions: subscribes to the trading store's currentPrice for
 *     the symbol; updates the live price marker every tick.
 *   - For CLOSED trades: no live updates (historical view only).
 *
 * If SL/TP changes while the modal is open (trailing stop moved), the
 * markers update live because they read from the store's positions array.
 */
'use client'

import { useEffect, useRef, useState } from 'react'
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type IPriceLine,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useTradingStore, type Position, type TradeRecord } from '@/stores/trading-store'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { ArrowUp, ArrowDown, TrendingUp, TrendingDown, X, Loader2 } from 'lucide-react'

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
  // Use 4 decimals for low-priced alts, 2 for big caps
  if (p >= 1000) return p.toFixed(2)
  if (p >= 1) return p.toFixed(4)
  if (p >= 0.01) return p.toFixed(5)
  return p.toFixed(8)
}

function fmtTime(iso: string): string {
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
  const { chartModalTrade, setChartModalTrade, currentPrice, positions } = useTradingStore()

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const entryLineRef = useRef<IPriceLine | null>(null)
  const tpLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)
  const catSlLineRef = useRef<IPriceLine | null>(null)
  const exitMarkerRef = useRef<any[]>([])

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [interval, setIntervalSel] = useState<'1m' | '5m' | '15m' | '1h'>('5m')
  const [candles, setCandles] = useState<Candle[]>([])

  const isOpen = chartModalTrade !== null
  const trade = chartModalTrade as (Position | TradeRecord) | null

  // ─── Detect open vs closed ───────────────────────────────────────────
  // A Position has `status` like 'OPEN' and no `close_price`.
  // A TradeRecord has `close_price` and `close_reason`.
  const isOpenPosition = !!(trade && (trade as Position).entry_price !== undefined &&
    (trade as Position).status === 'OPEN' && (trade as any).close_price === undefined)

  const isClosedTrade = !!(trade && (trade as TradeRecord).close_price !== undefined)

  // If open, read live SL/TP from the store's positions array (they may have
  // moved due to trailing stop / break-even adjustments)
  const livePosition: Position | null = isOpenPosition
    ? positions.find(p => p.symbol === (trade as Position).symbol &&
        p.direction === (trade as Position).direction &&
        p.entry_time === (trade as Position).entry_time) || null
    : null

  // ─── Fetch candles when modal opens or interval changes ──────────────
  useEffect(() => {
    if (!isOpen || !trade) return

    let cancelled = false
    setLoading(true)
    setError(null)
    setCandles([])

    const symbol = trade.symbol
    const url = `/api/candles?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=300`

    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (cancelled) return
        if (data.error && (!data.candles || data.candles.length === 0)) {
          setError(data.error)
          setLoading(false)
          return
        }
        const cs: Candle[] = (data.candles || []).map((c: any) => ({
          time: c.time as number,
          open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
        }))
        setCandles(cs)
        setLoading(false)
      })
      .catch(e => {
        if (cancelled) return
        setError('Fetch failed: ' + (e?.message || 'unknown'))
        setLoading(false)
      })

    return () => { cancelled = true }
  }, [isOpen, trade, interval])

  // ─── Create chart on mount, destroy on unmount ───────────────────────
  useEffect(() => {
    if (!isOpen || !chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
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
      width: chartContainerRef.current.clientWidth,
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

    // ─── Resize observer ──────────────────────────────────────────────
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const w = entry.contentRect.width
        if (chartRef.current && w > 0) {
          chartRef.current.applyOptions({ width: w })
        }
      }
    })
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      entryLineRef.current = null
      tpLineRef.current = null
      slLineRef.current = null
      catSlLineRef.current = null
      exitMarkerRef.current = []
    }
  }, [isOpen])

  // ─── Apply candle data ───────────────────────────────────────────────
  useEffect(() => {
    if (!seriesRef.current || candles.length === 0) return

    const data = candles.map(c => ({
      time: c.time as UTCTimestamp,
      open: c.open, high: c.high, low: c.low, close: c.close,
    }))
    seriesRef.current.setData(data)

    // Add exit marker for closed trades
    if (isClosedTrade && trade) {
      const exitTime = timeToSeconds((trade as TradeRecord).closed_at)
      const exitPrice = (trade as TradeRecord).close_price
      if (exitTime && exitPrice && seriesRef.current) {
        // Clear old markers
        exitMarkerRef.current = []
        // Set a single marker at exit time
        try {
          seriesRef.current.setMarkers([{
            time: exitTime as UTCTimestamp,
            position: 'aboveBar',
            color: '#a855f7',
            shape: 'circle',
            text: `EXIT ${fmtPrice(exitPrice)}`,
          }])
        } catch (e) {
          // Marker time must exist in the data series; if exit is outside
          // the candle range (very old trade), the marker is skipped.
        }
      }
    } else if (seriesRef.current) {
      try {
        seriesRef.current.setMarkers([])
      } catch {}
    }

    // Fit content to show all candles
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent()
    }
  }, [candles, trade, isClosedTrade])

  // ─── Update price lines (entry / TP / SL / cat SL) ───────────────────
  // Reads from `livePosition` if open, otherwise from `trade` snapshot.
  useEffect(() => {
    const series = seriesRef.current
    if (!series || !trade) return

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
    const tp = isOpenPosition ? (livePosition?.current_tp ?? (trade as any).current_tp) : null
    const sl = isOpenPosition ? (livePosition?.current_sl ?? (trade as any).current_sl) : null
    const catSl = isOpenPosition ? (livePosition?.catastrophic_sl ?? (trade as any).catastrophic_sl) : null

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
  }, [trade, livePosition, isOpenPosition, candles])

  // ─── Real-time price marker for open positions ───────────────────────
  // Updates the last candle's close + adds a yellow marker on each tick.
  useEffect(() => {
    if (!isOpenPosition || !trade || !seriesRef.current || candles.length === 0) return

    const symbol = trade.symbol
    // Only update if the current price is for this symbol (or assume it is,
    // since the engine snapshots the price for the open symbol)
    if (currentPrice <= 0) return

    // Update last candle's close to current price (live ticking)
    const lastCandle = candles[candles.length - 1]
    const updated = {
      ...lastCandle,
      close: currentPrice,
      high: Math.max(lastCandle.high, currentPrice),
      low: Math.min(lastCandle.low, currentPrice),
    }
    try {
      seriesRef.current.update({
        time: updated.time as UTCTimestamp,
        open: updated.open, high: updated.high, low: updated.low, close: updated.close,
      })
    } catch (e) {
      // Ignore — typically "time already passed" errors when crossing candle boundaries
    }
  }, [currentPrice, isOpenPosition, trade, candles])

  if (!trade) return null

  // ─── Derived display values ──────────────────────────────────────────
  const isLong = (trade as any).direction === 'LONG'
  const entryPrice = (trade as any).entry_price
  const entryTime = (trade as any).entry_time
  const sizeUsdt = (trade as any).size_usdt
  const pnlPct = (trade as any).pnl_pct ?? 0
  const pnlUsdt = (trade as any).pnl_usdt ?? 0
  const isWin = pnlPct >= 0

  const tp = isOpenPosition ? (livePosition?.current_tp ?? (trade as any).current_tp) : null
  const sl = isOpenPosition ? (livePosition?.current_sl ?? (trade as any).current_sl) : null
  const catSl = isOpenPosition ? (livePosition?.catastrophic_sl ?? (trade as any).catastrophic_sl) : null
  const exitPrice = isClosedTrade ? (trade as TradeRecord).close_price : null
  const exitTime = isClosedTrade ? (trade as TradeRecord).closed_at : null
  const closeReason = isClosedTrade ? (trade as TradeRecord).close_reason : null

  const strategyLabel = (trade as any).strategy || '—'

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
              {isOpenPosition ? 'OPEN' : 'CLOSED'}
            </Badge>
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
          </DialogDescription>
        </DialogHeader>

        {/* Chart area */}
        <div className="px-4 py-3">
          {loading && (
            <div className="flex items-center justify-center h-[420px] text-gray-400">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              <span className="font-mono text-xs">Loading candles from {trade.symbol}...</span>
            </div>
          )}

          {error && !loading && (
            <div className="flex flex-col items-center justify-center h-[420px] text-gray-400">
              <X className="w-8 h-8 text-red-500 mb-2" />
              <div className="font-mono text-xs text-red-400 mb-1">No candle data available</div>
              <div className="font-mono text-[10px] text-gray-500">{error}</div>
              <div className="font-mono text-[10px] text-gray-600 mt-3">
                Try a different timeframe, or check that this token has a Coinbase/Kraken pair.
              </div>
            </div>
          )}

          {!loading && !error && (
            <div ref={chartContainerRef} className="w-full h-[420px]" />
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
              {tp !== null && entryPrice > 0 && (
                <div className="text-gray-600 text-[9px] mt-0.5">
                  {isLong
                    ? `+${((tp - entryPrice) / entryPrice * 100).toFixed(2)}%`
                    : `+${((entryPrice - tp) / entryPrice * 100).toFixed(2)}%`}
                </div>
              )}
            </div>

            {/* Stop Loss */}
            <div className="bg-[#121a26] rounded p-2 border border-[#1e2a3d]">
              <div className="text-gray-500 mb-0.5">STOP LOSS</div>
              <div className="text-red-400 text-sm">{fmtPrice(sl)}</div>
              {sl !== null && entryPrice > 0 && (
                <div className="text-gray-600 text-[9px] mt-0.5">
                  {isLong
                    ? `${((sl - entryPrice) / entryPrice * 100).toFixed(2)}%`
                    : `${((entryPrice - sl) / entryPrice * 100).toFixed(2)}%`}
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
                <div className="text-gray-500 mb-0.5">CURRENT</div>
                <div className="text-yellow-400 text-sm">{fmtPrice(currentPrice)}</div>
                <div className="text-gray-600 text-[9px] mt-0.5">live</div>
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
                <span className="w-2 h-2 bg-yellow-400 rounded-full inline-block animate-pulse"></span> Live price
              </span>
            )}
            <span className="ml-auto text-gray-700">
              Source: Coinbase / Kraken · {candles.length} candles · {interval}
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
