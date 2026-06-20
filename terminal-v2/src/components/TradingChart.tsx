import { useEffect, useRef, useState, useCallback } from 'react';
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

/**
 * TradingChart — ENTREGABLE 1
 * 
 * Uses lightweight-charts to render candlestick data with animated
 * SL/TP price lines. Lines move via applyOptions() — NO re-render.
 */
export default function TradingChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const slLineRef = useRef<IPriceLine | null>(null);
  const tpLineRef = useRef<IPriceLine | null>(null);

  const [position, setPosition] = useState<PositionState>(getMockPosition());
  const [candles] = useState<CandlestickData<Time>[]>(generateMockCandles);

  // ─── Initialize Chart ──────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0a0f' },
        textColor: '#9ca3af',
        fontSize: 12,
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

    // Candlestick series
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    candleSeries.setData(candles);

    // Entry marker
    const entryIdx = 40;
    candleSeries.setMarkers([
      {
        time: candles[entryIdx]!.time,
        position: 'belowBar',
        color: '#3b82f6',
        shape: 'arrowUp',
        text: 'ENTRY',
      },
    ]);

    // SL line (Red) — current_sl
    const slLine = candleSeries.createPriceLine({
      price: position.current_sl,
      color: '#ef4444',
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: '  SL Inicial',
    });
    slLineRef.current = slLine;

    // TP line (Green) — current_tp
    const tpLine = candleSeries.createPriceLine({
      price: position.current_tp,
      color: '#10b981',
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: '  TP Inicial',
    });
    tpLineRef.current = tpLine;

    // Catastrophic SL line (Gray dashed) — never moves
    candleSeries.createPriceLine({
      price: position.catastrophic_sl,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Animate Price Line (smooth, no re-render) ────────────
  const animatePriceLine = useCallback(
    (priceLine: IPriceLine, targetPrice: number, newTitle: string, duration = 600) => {
      const startPrice = priceLine.options().price;
      const startTime = performance.now();

      const animate = (currentTime: number) => {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        // easeOutCubic for smooth deceleration
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

    animatePriceLine(slLineRef.current, position.entry_price, 'SL Break-Even ✅');

    setPosition((prev) => ({
      ...prev,
      current_sl: prev.entry_price,
      status: 'BREAK_EVEN_SECURED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, position.entry_price]);

  // ─── Walk-Forward: TP Extend ──────────────────────────────
  const handleTpExtend = useCallback(() => {
    if (!tpLineRef.current) return;

    const newTp = position.current_tp + 0.005;
    animatePriceLine(tpLineRef.current, newTp, 'TP Extended 📈');

    setPosition((prev) => ({
      ...prev,
      current_tp: newTp,
      status: 'TP_EXTENDED' as PositionStatus,
      sequence_index: prev.sequence_index + 1,
    }));
  }, [animatePriceLine, position.current_tp]);

  return (
    <div className="flex flex-col h-full w-full">
      {/* Chart container — fills available space */}
      <div
        ref={chartContainerRef}
        className="flex-1 min-h-[400px] rounded-lg overflow-hidden border border-terminal-border"
      />

      {/* Walk-Forward simulation controls */}
      <div className="mt-4 flex flex-col gap-3">
        {/* Position State Card */}
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4 font-mono text-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-gray-400">Position State</span>
            <span
              className={`px-2 py-0.5 rounded text-xs font-semibold ${
                position.status === 'ACTIVE'
                  ? 'bg-blue-500/20 text-blue-400'
                  : position.status === 'BREAK_EVEN_SECURED'
                  ? 'bg-yellow-500/20 text-yellow-400'
                  : 'bg-emerald-500/20 text-emerald-400'
              }`}
            >
              {position.status}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <span className="text-gray-500">Symbol</span>
              <p className="text-white">{position.symbol}</p>
            </div>
            <div>
              <span className="text-gray-500">Direction</span>
              <p className={position.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}>
                {position.direction}
              </p>
            </div>
            <div>
              <span className="text-gray-500">Entry</span>
              <p className="text-white">{position.entry_price.toFixed(6)}</p>
            </div>
            <div>
              <span className="text-gray-500">Size</span>
              <p className="text-white">${position.size_usdt}</p>
            </div>
            <div>
              <span className="text-gray-500">Current SL</span>
              <p className="text-red-400">{position.current_sl.toFixed(6)}</p>
            </div>
            <div>
              <span className="text-gray-500">Current TP</span>
              <p className="text-emerald-400">{position.current_tp.toFixed(6)}</p>
            </div>
            <div>
              <span className="text-gray-500">Catastrófico</span>
              <p className="text-gray-500">{position.catastrophic_sl.toFixed(6)}</p>
            </div>
            <div>
              <span className="text-gray-500">Seq. Index</span>
              <p className="text-white">{position.sequence_index}/{position.expected_sequence.length - 1}</p>
            </div>
          </div>
        </div>

        {/* Walk-Forward Sequence Strip */}
        <div className="bg-terminal-surface border border-terminal-border rounded-lg p-3">
          <span className="text-gray-500 text-xs font-mono mb-2 block">Walk-Forward Sequence</span>
          <div className="flex gap-1.5 overflow-x-auto">
            {position.expected_sequence.map((sym, idx) => (
              <div
                key={idx}
                className={`flex-shrink-0 px-2.5 py-1.5 rounded text-xs font-mono border transition-all duration-300 ${
                  idx < position.sequence_index
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
                    : idx === position.sequence_index
                    ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400 animate-pulse'
                    : 'bg-gray-800/50 border-gray-700/50 text-gray-600'
                }`}
              >
                {idx < position.sequence_index ? '✅' : idx === position.sequence_index ? '⏳' : '⬚'}{' '}
                {sym[0]},{sym[1]}
              </div>
            ))}
          </div>
        </div>

        {/* Simulation Buttons */}
        <div className="flex gap-3">
          <button
            onClick={handleBreakEven}
            disabled={position.status !== 'ACTIVE'}
            className={`flex-1 px-4 py-3 rounded-lg font-mono text-sm font-semibold transition-all duration-200 ${
              position.status === 'ACTIVE'
                ? 'bg-yellow-500/10 border border-yellow-500/40 text-yellow-400 hover:bg-yellow-500/20 hover:border-yellow-500/60 cursor-pointer'
                : 'bg-gray-800/30 border border-gray-700/30 text-gray-600 cursor-not-allowed'
            }`}
          >
            Simular Walk-Forward: Mover SL a Break-Even ({position.entry_price.toFixed(3)})
          </button>
          <button
            onClick={handleTpExtend}
            disabled={position.status === 'ACTIVE'}
            className={`flex-1 px-4 py-3 rounded-lg font-mono text-sm font-semibold transition-all duration-200 ${
              position.status !== 'ACTIVE'
                ? 'bg-emerald-500/10 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/20 hover:border-emerald-500/60 cursor-pointer'
                : 'bg-gray-800/30 border border-gray-700/30 text-gray-600 cursor-not-allowed'
            }`}
          >
            Simular Extensión de TP → {(position.current_tp + 0.005).toFixed(3)}
          </button>
        </div>
      </div>
    </div>
  );
}
