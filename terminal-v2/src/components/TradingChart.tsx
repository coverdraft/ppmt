import { useEffect, useRef, useCallback, useState } from 'react';
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type CandlestickData,
  type Time,
  ColorType,
  LineStyle,
} from 'lightweight-charts';
import { generateMockCandles, getMockPosition } from '../mock/candles';
import type { PositionState, PositionStatus } from '../types/position';

// ─── Types for live data ──────────────────────────────────────
interface CandleWire {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface TradingChartProps {
  position: PositionState;
  onPositionUpdate: (updater: (prev: PositionState) => PositionState) => void;
  /** Live candles from WebSocket (empty = use mock data) */
  liveCandles?: CandleWire[];
  /** Whether we're in live mode */
  isLive?: boolean;
}

/**
 * TradingChart — Candlestick chart with animated SL/TP price lines.
 *
 * Supports two modes:
 * - Mock: hardcoded 100 DOGE candles + simulation buttons
 * - Live: real candles from WebSocket, SL/TP from position_update messages
 */
export default function TradingChart({
  position,
  onPositionUpdate,
  liveCandles = [],
  isLive = false,
}: TradingChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const slLineRef = useRef<IPriceLine | null>(null);
  const tpLineRef = useRef<IPriceLine | null>(null);
  const catSlLineRef = useRef<IPriceLine | null>(null);
  const mockCandlesRef = useRef<CandlestickData<Time>[]>(generateMockCandles());
  const prevCandleCountRef = useRef(0);
  const slPriceRef = useRef(0);
  const tpPriceRef = useRef(0);
  const catSlPriceRef = useRef(0);

  // ─── Initialize Chart ──────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0a0f' },
        textColor: '#9ca3af',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1a1a2e' },
        horzLines: { color: '#1a1a2e' },
      },
      crosshair: {
        vertLine: { color: '#4b5563', style: LineStyle.Dashed },
        horzLine: { color: '#4b5563', style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor: '#1e1e2e',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#1e1e2e',
        timeVisible: true,
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
    });

    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    candleSeriesRef.current = candleSeries;

    // Load initial mock data (or will be replaced by live data)
    candleSeries.setData(mockCandlesRef.current);

    // Mock entry marker
    if (!isLive) {
      const entryIdx = 40;
      candleSeries.setMarkers([
        {
          time: mockCandlesRef.current[entryIdx]!.time,
          position: 'belowBar',
          color: '#3b82f6',
          shape: 'arrowUp',
          text: 'ENTRY',
        },
      ]);
    }

    // Initial SL/TP/Cat lines from mock position
    const initPos = getMockPosition();
    slPriceRef.current = initPos.current_sl;
    tpPriceRef.current = initPos.current_tp;
    catSlPriceRef.current = initPos.catastrophic_sl;

    if (!isLive) {
      const slLine = candleSeries.createPriceLine({
        price: initPos.current_sl,
        color: '#ef4444',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: '  SL Inicial',
      });
      slLineRef.current = slLine;

      const tpLine = candleSeries.createPriceLine({
        price: initPos.current_tp,
        color: '#10b981',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: '  TP Inicial',
      });
      tpLineRef.current = tpLine;

      const catSlLine = candleSeries.createPriceLine({
        price: initPos.catastrophic_sl,
        color: '#6b7280',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '  Catastrófico',
      });
      catSlLineRef.current = catSlLine;
    }

    chart.timeScale().fitContent();

    // Resize observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Live candles: update chart when new candles arrive ────
  useEffect(() => {
    if (!isLive || !candleSeriesRef.current || liveCandles.length === 0) return;

    const series = candleSeriesRef.current;
    const newCount = liveCandles.length;

    if (newCount > prevCandleCountRef.current) {
      // First load: set all data
      if (prevCandleCountRef.current === 0) {
        const chartData: CandlestickData<Time>[] = liveCandles.map((c) => ({
          time: c.time as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }));
        series.setData(chartData);

        // Fit content after initial load
        if (chartRef.current) {
          chartRef.current.timeScale().fitContent();
        }
      } else {
        // Incremental: add only new candles
        for (let i = prevCandleCountRef.current; i < newCount; i++) {
          const c = liveCandles[i]!;
          series.update({
            time: c.time as Time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          });
        }
      }

      prevCandleCountRef.current = newCount;
    }
  }, [isLive, liveCandles]);

  // ─── Live position: update SL/TP lines from position state ─
  useEffect(() => {
    if (!isLive || !candleSeriesRef.current) return;

    const series = candleSeriesRef.current;

    // No position → remove lines if they exist
    if (!position) return;

    const hasActivePos = ['ACTIVE', 'BREAK_EVEN_SECURED', 'TP_EXTENDED'].includes(position.status);

    if (hasActivePos) {
      // Create or update SL line
      if (position.current_sl !== slPriceRef.current) {
        if (slLineRef.current) {
          slLineRef.current.applyOptions({
            price: position.current_sl,
            title: position.status === 'BREAK_EVEN_SECURED' ? '  SL Break-Even \u2705' : '  SL',
          });
        } else {
          const slLine = series.createPriceLine({
            price: position.current_sl,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: '  SL',
          });
          slLineRef.current = slLine;
        }
        slPriceRef.current = position.current_sl;
      }

      // Create or update TP line
      if (position.current_tp !== tpPriceRef.current) {
        if (tpLineRef.current) {
          tpLineRef.current.applyOptions({
            price: position.current_tp,
            title: position.status === 'TP_EXTENDED' ? '  TP Extended \uD83D\uDCC8' : '  TP',
          });
        } else {
          const tpLine = series.createPriceLine({
            price: position.current_tp,
            color: '#10b981',
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: '  TP',
          });
          tpLineRef.current = tpLine;
        }
        tpPriceRef.current = position.current_tp;
      }

      // Create catastrophic SL line (once)
      if (catSlLineRef.current === null && position.catastrophic_sl) {
        const catLine = series.createPriceLine({
          price: position.catastrophic_sl,
          color: '#6b7280',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: '  Catastrófico',
        });
        catSlLineRef.current = catLine;
        catSlPriceRef.current = position.catastrophic_sl;
      }
    }
  }, [isLive, position]);

  // ─── Animate Price Line (mock mode only) ───────────────────
  const animatePriceLine = useCallback(
    (priceLine: IPriceLine, targetPrice: number, newTitle: string, duration = 600) => {
      const startPrice = priceLine.options().price;
      const startTime = performance.now();

      const animate = (currentTime: number) => {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const newPrice = startPrice + (targetPrice - startPrice) * eased;

        priceLine.applyOptions({
          price: parseFloat(newPrice.toFixed(6)),
          title: progress > 0.5 ? `  ${newTitle}` : priceLine.options().title,
        });

        if (progress < 1) {
          requestAnimationFrame(animate);
        }
      };

      requestAnimationFrame(animate);
    },
    []
  );

  // ─── Mock: Walk-Forward SL → Break-Even ────────────────────
  const handleBreakEven = useCallback(() => {
    if (!slLineRef.current || isLive) return;
    animatePriceLine(slLineRef.current, position.entry_price, 'SL Break-Even \u2705');
    onPositionUpdate((prev) => ({
      ...prev,
      current_sl: prev.entry_price,
      status: 'BREAK_EVEN_SECURED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, isLive, position.entry_price, onPositionUpdate]);

  // ─── Mock: TP Extend ───────────────────────────────────────
  const handleTpExtend = useCallback(() => {
    if (!tpLineRef.current || isLive) return;
    const newTp = position.current_tp + 0.005;
    animatePriceLine(tpLineRef.current, newTp, 'TP Extended \uD83D\uDCC8');
    onPositionUpdate((prev) => ({
      ...prev,
      current_tp: newTp,
      status: 'TP_EXTENDED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, isLive, position.current_tp, onPositionUpdate]);

  return (
    <div className="flex flex-col h-full w-full">
      {/* Chart container */}
      <div
        ref={chartContainerRef}
        className="flex-1 min-h-0 rounded-lg overflow-hidden border border-terminal-border"
      />

      {/* Simulation buttons — only in mock mode */}
      {!isLive && (
        <div className="mt-2 flex gap-2 flex-shrink-0">
          <button
            onClick={handleBreakEven}
            disabled={position.status !== 'ACTIVE'}
            className={`flex-1 px-3 py-2 rounded-lg font-mono text-xs font-semibold transition-all duration-200 ${
              position.status === 'ACTIVE'
                ? 'bg-yellow-500/10 border border-yellow-500/40 text-yellow-400 hover:bg-yellow-500/20 cursor-pointer'
                : 'bg-gray-800/30 border border-gray-700/30 text-gray-600 cursor-not-allowed'
            }`}
          >
            Walk-Forward: SL \u2192 Break-Even ({position.entry_price.toFixed(3)})
          </button>
          <button
            onClick={handleTpExtend}
            disabled={position.status === 'ACTIVE'}
            className={`flex-1 px-3 py-2 rounded-lg font-mono text-xs font-semibold transition-all duration-200 ${
              position.status !== 'ACTIVE'
                ? 'bg-emerald-500/10 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/20 cursor-pointer'
                : 'bg-gray-800/30 border border-gray-700/30 text-gray-600 cursor-not-allowed'
            }`}
          >
            Extender TP \u2192 {(position.current_tp + 0.005).toFixed(3)}
          </button>
        </div>
      )}

      {/* Live mode: connection indicator */}
      {isLive && (
        <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-gray-600">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span>Live data</span>
        </div>
      )}
    </div>
  );
}
