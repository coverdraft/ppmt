import { useEffect, useRef, useCallback } from 'react';
import {
  createChart,
  type IChartApi,
  type IPriceLine,
  type CandlestickData,
  type Time,
  ColorType,
  LineStyle,
} from 'lightweight-charts';
import { generateMockCandles, getMockPosition } from '../mock/candles';
import type { PositionState, PositionStatus } from '../types/position';

interface TradingChartProps {
  position: PositionState;
  onPositionUpdate: (updater: (prev: PositionState) => PositionState) => void;
}

/**
 * TradingChart — Pure candlestick chart with animated SL/TP price lines.
 *
 * ENTREGABLE 1: Chart renders 100 fake DOGE candles with SL/TP/Catastrophic lines.
 * ENTREGABLE 2: Position card moved to RightPanel. Chart stays lean.
 *
 * Lines move via applyOptions() — NO re-render, NO flickering.
 */
export default function TradingChart({ position, onPositionUpdate }: TradingChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const slLineRef = useRef<IPriceLine | null>(null);
  const tpLineRef = useRef<IPriceLine | null>(null);
  const candlesRef = useRef<CandlestickData<Time>[]>(generateMockCandles());

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
    candleSeries.setData(candlesRef.current);

    // Entry marker
    const entryIdx = 40;
    candleSeries.setMarkers([
      {
        time: candlesRef.current[entryIdx]!.time,
        position: 'belowBar',
        color: '#3b82f6',
        shape: 'arrowUp',
        text: 'ENTRY',
      },
    ]);

    // SL line (Red)
    const initialPos = getMockPosition();
    const slLine = candleSeries.createPriceLine({
      price: initialPos.current_sl,
      color: '#ef4444',
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: '  SL Inicial',
    });
    slLineRef.current = slLine;

    // TP line (Green)
    const tpLine = candleSeries.createPriceLine({
      price: initialPos.current_tp,
      color: '#10b981',
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: '  TP Inicial',
    });
    tpLineRef.current = tpLine;

    // Catastrophic SL line (Gray dashed)
    candleSeries.createPriceLine({
      price: initialPos.catastrophic_sl,
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: '  Catastrófico',
    });

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
    };
  }, []);

  // ─── Animate Price Line (smooth, no re-render) ────────────
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

  // ─── Walk-Forward: SL → Break-Even ────────────────────────
  const handleBreakEven = useCallback(() => {
    if (!slLineRef.current) return;

    animatePriceLine(slLineRef.current, position.entry_price, 'SL Break-Even \u2705');

    onPositionUpdate((prev) => ({
      ...prev,
      current_sl: prev.entry_price,
      status: 'BREAK_EVEN_SECURED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, position.entry_price, onPositionUpdate]);

  // ─── Walk-Forward: TP Extend ──────────────────────────────
  const handleTpExtend = useCallback(() => {
    if (!tpLineRef.current) return;

    const newTp = position.current_tp + 0.005;
    animatePriceLine(tpLineRef.current, newTp, 'TP Extended \uD83D\uDCC8');

    onPositionUpdate((prev) => ({
      ...prev,
      current_tp: newTp,
      status: 'TP_EXTENDED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, position.current_tp, onPositionUpdate]);

  return (
    <div className="flex flex-col h-full w-full">
      {/* Chart container */}
      <div
        ref={chartContainerRef}
        className="flex-1 min-h-0 rounded-lg overflow-hidden border border-terminal-border"
      />

      {/* Simulation buttons — compact, below chart */}
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
    </div>
  );
}
